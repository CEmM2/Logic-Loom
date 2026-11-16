"""THE single source of truth for "has this been written to?" and "is it current?".

Several modules once computed their own versions of these verdicts and the
copies disagreed — always in the reassuring direction. The answers now live
here, once, and everything else forwards them:

* :func:`write_history` — the only never-written determination.
* :func:`verify_graph_content` — content verification of the published graph.
* :func:`verify_generated_coverage` — lock-free check that the published
  generated tree covers exactly the registry's lessons.
* :func:`lock_blocked_check_status` — classification of a compiler diagnostic
  that could not run.
* :func:`resolve_currency` — the only place a ``current`` verdict is produced.
  Every signal is a required argument, so a caller cannot silently omit one.

Two rules run through everything here:

**No verdict is derived from a disposable artifact.** ``paths.lock`` and the
runtime tree are scratch: routinely gitignored, absent in any fresh clone, and
recreated by the next write. Their absence says nothing about content or
history.

**When signals disagree, the least reassuring one wins.** Evidence that content
moved outranks evidence of absence; anything undeterminable reads as
``unverifiable``, never as ``current``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..adapters import failure_memory as adapter
from ..adapters.failure_memory import FailureMemoryAdapterError, ResolvedProject
from ..models.failure_memory import StalenessState

logger = logging.getLogger(__name__)

CurrencyState = str  # "current" | "stale" | "unverifiable" | "uncompiled" | "never_written"


@dataclass(frozen=True, slots=True)
class WriteHistory:
    """Whether anything has ever been written to a project, and the evidence.

    ``never_written`` requires the canonical registry FILE to be absent, plus no
    route index, no generated nodes and no graph.

    The registry must be absent rather than merely empty: an empty registry is
    indistinguishable on disk from a truncated one, and absence of current
    artifacts is not absence of history. A project that has a registry file at
    all reports ``uncompiled`` instead — still non-blocking, still recordable,
    claiming nothing that is not true.
    """

    never_written: bool
    evidence: dict[str, bool] = field(default_factory=dict)
    reason: str = ""

    @property
    def contradicting_evidence(self) -> list[str]:
        """Artifacts whose presence contradicts a never-written claim."""
        return sorted(name for name, present in self.evidence.items() if present)


@dataclass(frozen=True, slots=True)
class GraphVerification:
    """Whether the published graph actually CONTAINS what the routes reference."""

    state: str  # "verified" | "missing_nodes" | "empty" | "absent" | "timestamp_mismatch" | "content_mismatch"
    reason: str
    graph_node_count: int = 0
    routed_node_count: int = 0
    missing_node_ids: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.state == "verified"

    @property
    def label(self) -> str:
        """The GraphState vocabulary for this verification.

        The mapping lives here rather than in the presentation service because
        it emits the word ``current``, and every place that can emit that word
        belongs beside the logic that earns it.
        """
        if self.state == "verified":
            return "current"
        if self.state in {"missing_nodes", "content_mismatch"}:
            return "stale"
        if self.state == "absent":
            return "absent"
        return "unverifiable"


@dataclass(frozen=True, slots=True)
class GeneratedCoverage:
    """Does the published generated tree cover exactly the registry's lessons?

    A pure, lock-free content comparison: the node ids present as files in the
    generated-nodes directory versus the node ids the registry's lessons demand.
    It needs no toolchain and no project lock, so it still works in a cold
    clone where the recompile-based ``/check`` cannot run.

    ``incomplete`` is definitive, not advisory: the compiler writes one node
    file per lesson, so a missing file means resolution will fail for that
    lesson, and an orphan file means the published tree describes a registry
    that no longer exists. ``absent`` claims nothing — an uncompiled project
    has no generated tree, and that state has its own honest vocabulary.
    """

    state: str  # "complete" | "incomplete" | "absent"
    reason: str
    missing_node_ids: list[str] = field(default_factory=list)
    orphan_node_ids: list[str] = field(default_factory=list)


def lesson_node_id(project: ResolvedProject, lesson_id: str) -> str:
    """``<node_namespace>-<lesson id, lowercased>`` — the package's node naming.

    Lives in the owner module so exactly one place spells the mapping the
    coverage and graph checks depend on.
    """
    return f"{project.node_namespace}-{lesson_id.lower()}"


def write_history(project: ResolvedProject) -> WriteHistory:
    """THE never-written determination. Every consumer calls this one function.

    Deliberately consults only canonical and published artifacts. The project
    lock is not among them and must never be, which is why
    ``adapter.lock_parent_exists`` is not called here: its absence is an
    operational fact about lock-taking calls, not evidence about history.
    """
    presence = adapter.artifact_presence(project)
    evidence = {
        "registry": presence["registry"],
        "routes": presence["routes"],
        "generated_nodes": presence["generated_nodes"],
        "graph": presence["graph"],
    }
    if any(evidence.values()):
        return WriteHistory(
            never_written=False,
            evidence=evidence,
            reason=(
                "This project owns "
                + ", ".join(sorted(name for name, present in evidence.items() if present))
                + " — something wrote them."
            ),
        )
    return WriteHistory(
        never_written=True,
        evidence=evidence,
        reason=(
            "This project owns no registry, route index, generated nodes or graph — "
            "nothing has ever been written here."
        ),
    )


def _graph_nodes(project: ResolvedProject) -> dict[str, dict[str, Any]] | None:
    """The node OBJECTS actually present in the published graph document.

    Reads the ``nodes`` array, not the ``graph.node_count`` header. That
    distinction is the entire point of this function: the header is
    caller-controlled metadata and a graph claiming six nodes while containing
    zero satisfied every metadata check while being incapable of resolving
    anything. The full objects are returned (not just the ids) because
    resolution reads node CONTENT out of the graph — ``load_with`` for
    coactivation, ``tags`` for advisory matching — and a graph whose node
    exists but whose content was altered resolves differently while passing
    every id-containment check.
    """
    try:
        graph_path = project.path("graph")
    except FailureMemoryAdapterError:
        return None
    if not graph_path.is_file():
        return None
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return None
    return {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }


def verify_generated_coverage(
    project: ResolvedProject, registry_lesson_ids: list[str]
) -> GeneratedCoverage:
    """Compare the generated tree's node files against the registry's lessons.

    Reads a directory listing of an explicitly configured path — no lock, no
    toolchain, no discovery. This is the signal that keeps generated-tree
    integrity consultable even where the recompile-based ``/check`` is blocked
    by an absent lock parent.
    """
    try:
        directory = project.path("generated_nodes")
    except FailureMemoryAdapterError:
        return GeneratedCoverage(
            state="absent", reason="The generated-nodes path cannot be resolved."
        )
    if not directory.is_dir():
        return GeneratedCoverage(
            state="absent", reason="No generated-nodes directory has been published."
        )
    present = {path.stem for path in directory.glob(f"{project.node_namespace}-*.md")}
    if not present:
        return GeneratedCoverage(
            state="absent", reason="The generated-nodes directory holds no node files."
        )
    expected = {lesson_node_id(project, lesson_id) for lesson_id in registry_lesson_ids}
    missing = sorted(expected - present)
    orphans = sorted(present - expected)
    if missing or orphans:
        parts = []
        if missing:
            parts.append(
                f"{len(missing)} lesson(s) in the registry have NO generated node file "
                f"({', '.join(missing[:5])}{', …' if len(missing) > 5 else ''}) — "
                "resolution fails for exactly those lessons"
            )
        if orphans:
            parts.append(
                f"{len(orphans)} generated node file(s) describe lessons the registry "
                f"no longer holds ({', '.join(orphans[:5])}"
                + (", …" if len(orphans) > 5 else "")
                + ")"
            )
        return GeneratedCoverage(
            state="incomplete",
            reason="; ".join(parts) + ". The published tree disagrees with the registry.",
            missing_node_ids=missing,
            orphan_node_ids=orphans,
        )
    return GeneratedCoverage(
        state="complete",
        reason=(
            f"Every one of the registry's {len(expected)} lesson(s) has its generated "
            "node file, and no orphan files remain."
        ),
    )


def lock_blocked_check_status(project: ResolvedProject, code: str) -> str:
    """Classify a compiler diagnostic that could not run. Never a green tick.

    ``never_written`` is reserved for a project that owns NO artifacts — the
    same :func:`write_history` determination as everywhere else, never a local
    rule. When artifacts exist and only the disposable lock directory is
    missing, the honest answer is ``unverifiable``: the check could not run, so
    it proves nothing either way. Anything else is ``invalid``.
    """
    if write_history(project).never_written:
        return "never_written"
    if code in {"lock_parent_missing", "lock_contention"}:
        # Could not RUN — an absent scratch directory or a concurrent writer
        # holding the lock. Neither proves anything about the committed bytes.
        return "unverifiable"
    return "invalid"


def routed_node_ids(routes: dict[str, Any] | None) -> set[str]:
    """Every lesson node the compiled route index points at."""
    routed: set[str] = set()
    for bucket in ("by_path", "by_symbol"):
        for entries in ((routes or {}).get(bucket) or {}).values():
            for entry in entries or []:
                node_id = str((entry or {}).get("node_id") or "")
                if node_id:
                    routed.add(node_id)
    return routed


def verify_graph_content(
    project: ResolvedProject,
    *,
    graph_metadata: dict[str, Any] | None,
    routes: dict[str, Any] | None,
    derived_generated_at: str | None,
    routes_state: str = "unknown",
) -> GraphVerification:
    """Verify the graph by its CONTENT, then by its metadata — in that order.

    A resolution reads the graph to find the nodes its routes name. A graph that
    does not contain them cannot resolve, whatever its header says. So the first
    question asked here is "are the routed nodes actually in the document?", and
    only a graph that passes it is eligible to be called current.

    Metadata about content is not content: a zero-node graph carrying a
    correct-looking header must never read as ``current``.
    """
    if graph_metadata is None:
        return GraphVerification(
            state="absent",
            reason=(
                "No graph has been published, so this project cannot resolve at all. "
                "That is not the same as being up to date."
            ),
        )

    graph_nodes = _graph_nodes(project)
    if graph_nodes is None:
        return GraphVerification(
            state="absent",
            reason="The published graph could not be read as a node-link document.",
        )
    node_ids = set(graph_nodes)

    routed = routed_node_ids(routes)

    if not node_ids:
        # An empty graph with routed lessons is not merely unverifiable — it is
        # definitively wrong: every routed node is missing, so resolution fails
        # for every route. It is classified with the other definitive findings so
        # the verdict is `stale`, not the softer `unverifiable`.
        return GraphVerification(
            state="missing_nodes" if routed else "empty",
            reason=(
                "The published graph contains ZERO nodes. Its metadata may claim "
                f"otherwise (node_count={graph_metadata.get('node_count')}), but the "
                "document holds nothing to resolve against — metadata about content is "
                "not content."
            ),
            graph_node_count=0,
            routed_node_count=len(routed),
            missing_node_ids=sorted(routed),
        )

    missing = sorted(routed - node_ids)
    if missing:
        return GraphVerification(
            state="missing_nodes",
            reason=(
                f"{len(missing)} lesson node(s) referenced by the compiled route index are "
                f"absent from the published graph ({', '.join(missing[:5])}"
                + (", …" if len(missing) > 5 else "")
                + "). Resolution would fail for exactly those routes."
            ),
            graph_node_count=len(node_ids),
            routed_node_count=len(routed),
            missing_node_ids=missing,
        )

    # ── published-artifact agreement: the package's own single definition ──
    #
    # Resolution reads content out of the graph — `load_with` drives
    # coactivation, `tags` drive advisory matching — so a graph whose nodes
    # merely EXIST can still resolve differently after its content is altered.
    # `provider.validate_publication` owns that question (identity, graph
    # nodes vs published generated files, shared-field agreement, load_with
    # target presence) as the one definition every consumer inherits, so
    # nothing here re-implements it: the package's problems ARE the finding.
    problems = adapter.publication_problems(project)
    if problems:
        shown = "; ".join(problems[:5]) + ("; …" if len(problems) > 5 else "")
        return GraphVerification(
            state="content_mismatch",
            reason=(
                f"The package's publication validation reports {len(problems)} "
                f"problem(s): {shown}. The published graph does not agree with the "
                "published generated nodes (or carries a foreign identity), so it "
                "reads as stale until republished."
            ),
            graph_node_count=len(node_ids),
            routed_node_count=len(routed),
        )

    if derived_generated_at is None:
        return GraphVerification(
            state="timestamp_mismatch",
            reason=(
                "No registry digest is available to derive the expected graph timestamp "
                "from, so graph currency cannot be established."
            ),
            graph_node_count=len(node_ids),
            routed_node_count=len(routed),
        )

    if str(graph_metadata.get("generated_at")) != derived_generated_at:
        if routes_state == "stale":
            # We are not guessing here: the route index already proves the
            # registry moved, so the graph is out of date rather than merely
            # unverifiable. Reporting the sub-signal as `unverifiable` beside a
            # top-level `stale` would look like this surface contradicting
            # itself — which is the exact shape of the defect being fixed.
            return GraphVerification(
                state="missing_nodes",
                reason=(
                    "The published graph predates the current registry: the route index "
                    "already proves the content moved, and this graph's generated_at "
                    f"differs from the instant derived from the new digest "
                    f"({derived_generated_at})."
                ),
                graph_node_count=len(node_ids),
                routed_node_count=len(routed),
            )
        return GraphVerification(
            state="timestamp_mismatch",
            reason=(
                "The published graph's generated_at does not match the instant derived from "
                f"the current registry digest ({derived_generated_at}). Either the content "
                "moved or the graph was published with a different explicit timestamp — "
                "Logic-Loom cannot tell which, and will not guess 'current'."
            ),
            graph_node_count=len(node_ids),
            routed_node_count=len(routed),
        )

    return GraphVerification(
        state="verified",
        reason=(
            f"The published graph contains all {len(routed)} routed lesson node(s), carries "
            "this project's repository identity, and its generated_at matches the instant "
            "derived from the current registry digest."
        ),
        graph_node_count=len(node_ids),
        routed_node_count=len(routed),
    )


def route_index_staleness(
    registry_sha256: str | None, routes: dict[str, Any] | None
) -> StalenessState:
    """Compare the route index's recorded registry digest to the live registry.

    Lives here, with the other verdicts, rather than in the presentation service:
    this is the strongest and cheapest currency signal there is — pure content
    comparison, needing no toolchain and no lock — and keeping it beside
    :func:`resolve_currency` is what stops a second module growing its own idea
    of what "current" means.
    """
    if routes is None:
        return StalenessState(
            state="uncompiled",
            registry_sha256=registry_sha256,
            reason="No compiled route index exists yet; run `failure-memory compile`.",
        )
    recorded = routes.get("source_hash")
    recorded_str = str(recorded) if recorded is not None else None
    if registry_sha256 is None or recorded_str is None:
        return StalenessState(
            state="unknown",
            registry_sha256=registry_sha256,
            routes_source_hash=recorded_str,
            reason="Registry or route-index digest is unavailable.",
        )
    if recorded_str == f"sha256:{registry_sha256}":
        return StalenessState(
            state="current",
            registry_sha256=registry_sha256,
            routes_source_hash=recorded_str,
            reason="The compiled route index was built from the current registry.",
        )
    return StalenessState(
        state="stale",
        registry_sha256=registry_sha256,
        routes_source_hash=recorded_str,
        reason=(
            "The registry has changed since the route index was compiled. Every "
            "generated node embeds the whole-registry digest, so recompiling "
            "rewrites the entire generated tree, not just the changed lesson."
        ),
    )


def resolve_currency(
    *,
    history: WriteHistory,
    routes_state: str,
    graph: GraphVerification,
    check_status: str | None,
    generated: GeneratedCoverage,
    lesson_count: int | None = None,
) -> tuple[CurrencyState, str]:
    """THE currency decision. Ordered by strength of evidence, then contradiction.

    Every signal this surface possesses is a REQUIRED argument, so a caller
    cannot forget one without a ``TypeError``.

    Precedence, strongest evidence first — never cheapest probe first:

    1. **Positive proof the knowledge moved or the published content is wrong**
       (a stale route index; a graph under a foreign identity, missing routed
       nodes or disagreeing content; a ``/check`` that reports drift against
       published artifacts; a generated tree that does not cover the registry;
       generated nodes surviving without their route index).
    2. **Positive proof currency cannot be established** (an unreadable digest,
       an invalid registry).
    3. **Contradiction**: a never-written claim contradicted by ANY other
       signal — surviving artifacts, a non-zero lesson count, a ``/check``
       that reports drift — loses, and the verdict is ``unverifiable``.
    4. Only now may genuine absence-of-everything speak.
    5. ``current`` requires POSITIVE verification of everything: a route index
       matching the registry digest, a graph whose CONTENT agrees with the
       published nodes, a generated tree covering the registry, and a compiler
       ``/check`` that actually ran and reported the literal ``clean``.
       Anything else — ``unverifiable``, ``invalid``, ``None``, unknown
       vocabulary — blocks ``current``: "could not determine" is never "fine".
    """
    if routes_state == "stale":
        return (
            "stale",
            "The registry has changed since the route index was compiled — the recorded "
            "knowledge has genuinely moved. This is content evidence and is not overridden "
            "by any missing scratch directory.",
        )
    if graph.state in {"missing_nodes", "content_mismatch"}:
        return "stale", graph.reason
    # `drift` is definitive only against something actually published. On a
    # never-compiled project the check trivially reports drift ("a compile
    # would create files"), and calling THAT stale would erase the honest
    # `uncompiled` vocabulary. With a route index or generated tree present,
    # drift means the published content disagrees with the registry — stale.
    anything_published = routes_state != "uncompiled" or generated.state != "absent"
    if check_status == "drift" and anything_published:
        return "stale", (
            "The compiler's own check reports DRIFT: recompiling the registry does not "
            "reproduce the committed generated nodes and route index, so the published "
            "content — not merely its metadata — disagrees with the registry. Corrupted "
            "generated nodes and removed route coverage both surface exactly here."
        )
    if generated.state == "incomplete":
        return "stale", generated.reason
    if generated.state != "absent" and routes_state == "uncompiled":
        return "stale", (
            "Generated node files exist but the compiled route index does not. The "
            "compiler publishes both together, so this project's published artifacts are "
            "torn — a compile demonstrably happened and part of its output is gone."
        )
    if routes_state == "unknown":
        return "unverifiable", "The registry or route-index digest could not be read."

    if history.never_written:
        contradictions: list[str] = []
        if history.contradicting_evidence:
            contradictions.append(
                "artifacts exist: " + ", ".join(history.contradicting_evidence)
            )
        if lesson_count:
            contradictions.append(f"the registry holds {lesson_count} lesson(s)")
        if check_status in {"drift", "invalid"}:
            contradictions.append(f"the compiler reports {check_status!r}")
        if generated.state != "absent":
            contradictions.append("a generated tree exists")
        if contradictions:
            return (
                "unverifiable",
                "A never-written verdict is contradicted by this surface's own signals ("
                + "; ".join(contradictions)
                + "). When signals disagree the least reassuring one wins, so currency is "
                "reported as undetermined rather than as a clean slate.",
            )
        return "never_written", history.reason

    if check_status == "invalid":
        return "unverifiable", (
            "The compiler rejects this registry or could not compare the generated tree, "
            "so nothing about the published content can be verified."
        )
    if routes_state == "uncompiled":
        return (
            "uncompiled",
            "A registry exists but no route index has been compiled from it yet. Nothing "
            "here claims to be current.",
        )
    if not graph.verified:
        return "unverifiable", graph.reason
    if check_status != "clean":
        return "unverifiable", (
            "The compiler's check did not positively report 'clean' "
            f"(it reported {check_status!r}), so the published generated content cannot "
            "be verified against the registry. A verdict of 'current' requires every "
            "signal to verify, and an unobtainable or unrecognised one blocks it."
        )
    return "current", (
        "Route index verified against the registry digest, the compiler check reports "
        "clean, the generated tree covers the registry, and " + graph.reason
    )


__all__ = [
    "CurrencyState",
    "GeneratedCoverage",
    "GraphVerification",
    "WriteHistory",
    "lesson_node_id",
    "lock_blocked_check_status",
    "resolve_currency",
    "route_index_staleness",
    "routed_node_ids",
    "verify_generated_coverage",
    "verify_graph_content",
    "write_history",
]
