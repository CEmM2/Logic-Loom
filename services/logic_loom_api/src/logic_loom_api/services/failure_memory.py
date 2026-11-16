"""Failure Memory workspace service — presentation logic, zero package logic.

This module turns what ``adapters/failure_memory.py`` reads out of the package
into the shapes ``models/failure_memory.py`` declares. It is deliberately its
own service rather than more code inside the graph adapter.

What it does NOT do, and must never start doing:

* derive routes, node ids, tags, fingerprints or selection classes — every one
  of those is compiler/provider output that is read, not recomputed;
* write anything, anywhere;
* decide whether a lesson applies. The route index and the provider decide;
  this module reports what they decided and, where the answer is surprising,
  explains it.

The one piece of classification logic here is
:func:`is_documentation_path`, and it is EXPLANATORY ONLY — see its docstring.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from ..adapters import failure_memory as adapter
from . import failure_memory_currency as currency
from ..adapters.failure_memory import FailureMemoryAdapterError, ResolvedProject
from ..config import Settings
from ..models.failure_memory import (
    ArtifactState,
    CheckResponse,
    DeliveryReport,
    GraphState,
    LessonDetail,
    LessonFacets,
    LessonFoundBy,
    LessonLocation,
    LessonReferences,
    LessonSummary,
    LessonsResponse,
    Overlay,
    OverlayEdge,
    OverlayNode,
    ProjectSummary,
    ProjectsResponse,
    RecordView,
    ResolveRequest,
    ResolveResponse,
    RouteCoverage,
    RouteEntry,
    RouteReason,
    RoutesResponse,
    StalenessState,
    StatusResponse,
)

logger = logging.getLogger(__name__)

#: Mirrors ``akms.task_context.resolve._DOCUMENTATION_ROOTS`` /
#: ``_DOCUMENTATION_SUFFIXES``. Copied rather than imported on purpose: those
#: are PRIVATE AKMS internals and the failure-memory package boundary forbids
#: downstream consumers from importing private AKMS orchestration helpers.
#: Because it is a copy, it is a DISPLAY heuristic and is pinned against the
#: frozen conformance pack's documentation cases by
#: ``test_documentation_classifier_matches_the_frozen_pack``. Nothing in this
#: module routes on it — see :func:`is_documentation_path`.
DOCUMENTATION_ROOTS = frozenset({"doc", "docs", "documentation"})
DOCUMENTATION_SUFFIXES = frozenset({".adoc", ".asciidoc", ".md", ".mdx", ".rst"})

_REQUEST_SCHEMA_VERSION = "failure-memory-provider-request/v1"


#: 100 years expressed in MICROSECONDS. Three properties, each learned the hard
#: way while writing this:
#:
#: * **Not a power of two.** Reducing modulo 2**k is plain truncation to the low
#:   bits, so the digest's leading bytes would not affect the result at all and
#:   two registries differing only in their first byte would derive the SAME
#:   instant.
#: * **Not 2**31 - 1 either.** A Mersenne modulus has 2**31 ≡ 1, so digest bits
#:   b and b+31 change the residue identically — structured collisions every 31
#:   bits.
#: * **Wide.** 3.16e15 ≈ 2**51.5 possible instants, so the birthday odds of two
#:   distinct registry states deriving the same instant are ~2**-51 per pair
#:   rather than ~2**-31.
#:
#: All three matter in the same direction: a colliding derivation makes
#: genuinely changed content read as CURRENT, and unlike a wall-clock value
#: (which merely reads as permanently stale) that failure does not announce
#: itself.
_GENERATED_AT_RANGE_US = 3_155_760_000_000_000

#: The instant a content digest of all-zeroes maps to. Arbitrary but fixed —
#: what matters is only that the mapping is a pure function of registry content.
_GENERATED_AT_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def derive_generated_at(source_hash: str) -> str:
    """Map a registry content digest to a stable timezone-aware instant.

    ``generated_at`` is caller-supplied and MANDATORY, and same content with a
    different value reads as STALE — so it must be a pure function of content,
    never the wall clock, or every refresh would manufacture false staleness.
    The input is the registry digest recorded as ``routes.json``'s
    ``source_hash`` — the same input the WhichWhereWhatter surface derives
    from, so the two cannot disagree about what "current" means. (The exact
    digest→instant function is not yet pinned across the two consumers;
    ``POST /refresh`` accepts an explicit ``generated_at`` so a caller can
    supply another surface's value verbatim.)
    """
    digest = source_hash.split(":", 1)[-1].strip().lower()
    if not digest:
        raise FailureMemoryAdapterError(
            "Cannot derive generated_at without a registry digest",
            code="generated_at_underivable",
        )
    try:
        value = int(digest, 16)
    except ValueError as exc:
        raise FailureMemoryAdapterError(
            f"Registry digest is not hexadecimal: {source_hash!r}",
            code="generated_at_underivable",
        ) from exc
    microseconds = value % _GENERATED_AT_RANGE_US
    return (_GENERATED_AT_EPOCH + timedelta(microseconds=microseconds)).isoformat()


def is_documentation_path(path: str) -> bool:
    """True when AKMS would classify *path* as documentation.

    EXPLANATORY ONLY. No delivery decision in this module is taken from this
    function: :func:`build_resolve` derives its verdict by comparing the
    compiled route index against the provider's actual records, which is
    empirical. This classifier exists so the UI can say *why* a lesson with a
    live route was suppressed, and it deliberately reproduces AKMS's real rule
    including its surprising parts — ANY ``.md`` / ``.mdx`` / ``.rst`` /
    ``.adoc`` / ``.asciidoc`` file counts, wherever it lives, not just files
    under ``docs/``.
    """
    lowered = path.casefold()
    parts = lowered.split("/")
    basename = parts[-1]
    return (
        parts[0] in DOCUMENTATION_ROOTS
        or basename.startswith("readme")
        or PurePosixPath(lowered).suffix in DOCUMENTATION_SUFFIXES
    )


# ── overlay identity ────────────────────────────────────────────────────────


def overlay_node_id(repository_id: str, kind: str, key: str) -> str:
    """``"<repository_id>::<kind>:<key>"``.

    The repository prefix is the whole point. ``src/config.py`` in
    WhichWhereWhatter and ``src/config.py`` in Logic-Loom are different files
    with different lessons; an overlay keyed on the bare path would silently
    merge them the first time two projects are displayed together.
    """
    return f"{repository_id}::{kind}:{key}"


def _file_node_id(repository_id: str, path: str) -> str:
    return overlay_node_id(repository_id, "file", path)


def _symbol_node_id(repository_id: str, symbol: str) -> str:
    return overlay_node_id(repository_id, "symbol", symbol)


def _lesson_node_id(repository_id: str, node_id: str) -> str:
    return overlay_node_id(repository_id, "lesson", node_id)


# ── discovery ───────────────────────────────────────────────────────────────


def build_projects(settings: Settings) -> ProjectsResponse:
    """Enumerate the EXPLICITLY configured projects. Never touches the filesystem
    looking for more of them."""
    installed = adapter.failure_memory_available()
    enabled = settings.failure_memory_enabled()
    reason: str | None = None
    if not enabled:
        reason = "failure_memory_mode is 'disabled' in Logic-Loom's configuration."
    elif not installed:
        reason = adapter.DEGRADATION_ENVELOPE["reason"]["message"]
    elif not settings.failure_memory_projects:
        reason = (
            "No failure-memory projects are configured. Add [[failure_memory_projects]] "
            "entries with `id` and `config` to config.toml — Logic-Loom never discovers "
            "projects by scanning the filesystem."
        )

    summaries: list[ProjectSummary] = []
    for entry in settings.failure_memory_projects:
        summary = ProjectSummary(id=entry.id, config_path=str(entry.config_path()))
        if installed and enabled:
            try:
                project = adapter.load_configured_project(entry)
            except FailureMemoryAdapterError as exc:
                summary.error_code = exc.code
                summary.error = str(exc)
            else:
                summary.available = True
                summary.root = str(project.root)
                summary.repository_id = project.repository_id
                summary.node_namespace = project.node_namespace
                summary.config_fingerprint = project.config.fingerprint
                summary.never_written = currency.write_history(project).never_written
                summary.lock_parent_exists = adapter.lock_parent_exists(project)
        summaries.append(summary)

    return ProjectsResponse(
        mode=settings.failure_memory_mode,
        enabled=enabled,
        operator=settings.failure_memory_operator(),
        available=enabled and installed,
        package_installed=installed,
        package_version=adapter.package_version(),
        reason=reason,
        degradation=None if installed else adapter.degradation_envelope(),
        projects=summaries,
    )


# ── status ──────────────────────────────────────────────────────────────────


def build_status(settings: Settings, project: ResolvedProject) -> StatusResponse:
    status = adapter.artifact_status(project)
    routes = adapter.route_index(project)
    lessons = adapter.registry_lessons(project)
    graph = adapter.graph_metadata(project)
    generated_policy = {str(k): str(v) for k, v in project.config.generated.items()}

    artifacts = [
        ArtifactState(
            key=key,
            path=str(value.get("path")),
            exists=bool(value.get("exists")),
            sha256=value.get("sha256"),
            policy=generated_policy.get(_POLICY_KEY.get(key, "")),
        )
        for key, value in (status.get("artifacts") or {}).items()
    ]
    registry_sha256 = (status.get("artifacts") or {}).get("registry", {}).get("sha256")

    routes_staleness = currency.route_index_staleness(registry_sha256, routes)
    derived = None
    if registry_sha256:
        # Derived from the LIVE registry digest, not the route index's recorded
        # one: the question is what the graph SHOULD carry now.
        try:
            derived = derive_generated_at(registry_sha256)
        except FailureMemoryAdapterError:
            derived = None

    # Every currency answer comes from the one module that owns them. Nothing
    # here recomputes a verdict, and nothing here consults the project lock.
    history = currency.write_history(project)
    verification = currency.verify_graph_content(
        project,
        graph_metadata=graph,
        routes=routes,
        derived_generated_at=derived,
        routes_state=routes_staleness.state,
    )
    generated_coverage = currency.verify_generated_coverage(
        project, [str(lesson.get("id") or "") for lesson in lessons]
    )
    # The compiler's own diagnostics — the SAME code path `POST /check` runs,
    # so the two can never disagree — consulted in the verdict below. The
    # literal 2.0 bounds how long this READ may wait on the project lock
    # (`run_compiler` alone would wait the configured toolchain timeout, 120 s
    # shipped, under contention); the lock is re-entrant per thread, so the
    # compiler's inner acquisitions are immediate once held here, and
    # contention degrades honestly to `unverifiable` — never to `current`.
    try:
        with adapter.bounded_project_lock(project, timeout_seconds=2.0):
            check = build_check(settings, project)
        check_status: str | None = check.status
        check_reason = _check_reason(check.status)
    except FailureMemoryAdapterError as exc:
        # lock_contention / lock_parent_missing / anything else: classified by
        # the owner, never locally, and never as anything reassuring.
        check_status = currency.lock_blocked_check_status(project, exc.code)
        check_reason = (
            _check_reason(check_status)
            + f" (diagnostics unobtainable: {exc.code} — {exc})"
        )
    overall, overall_reason = currency.resolve_currency(
        history=history,
        routes_state=routes_staleness.state,
        graph=verification,
        check_status=check_status,
        generated=generated_coverage,
        lesson_count=len(lessons),
    )

    if graph is None:
        graph_state = GraphState(
            published=False,
            currency=verification.label,  # type: ignore[arg-type]
            currency_reason=verification.reason,
        )
    else:
        graph_state = GraphState(
            currency=verification.label,  # type: ignore[arg-type]
            currency_reason=verification.reason,
            published=True,
            generated_at=graph.get("generated_at"),
            repo_id=graph.get("repo_id"),
            global_vault=graph.get("global_vault"),
            node_count=graph.get("node_count"),
            edge_count=graph.get("edge_count"),
            identity_matches_config=graph.get("repo_id") == project.repository_id,
            content_node_count=verification.graph_node_count,
            routed_node_count=verification.routed_node_count,
            missing_routed_node_ids=verification.missing_node_ids,
        )

    by_path = (routes or {}).get("by_path") or {}
    by_symbol = (routes or {}).get("by_symbol") or {}

    return StatusResponse(
        mode=settings.failure_memory_mode,
        project_id=project.id,
        repository_id=project.repository_id,
        node_namespace=project.node_namespace,
        config_path=str(project.config_path),
        root=str(project.root),
        config_fingerprint=project.config.fingerprint,
        lesson_count=len(lessons),
        never_written=history.never_written,
        lock_parent_exists=adapter.lock_parent_exists(project),
        currency=overall,  # type: ignore[arg-type]
        currency_reason=overall_reason,
        routed_path_count=len(by_path),
        routed_symbol_count=len(by_symbol),
        check_status=check_status,  # type: ignore[arg-type]
        check_reason=check_reason,
        generated_state=generated_coverage.state,  # type: ignore[arg-type]
        generated_reason=generated_coverage.reason,
        generated_missing_node_ids=generated_coverage.missing_node_ids,
        generated_orphan_node_ids=generated_coverage.orphan_node_ids,
        artifacts=artifacts,
        staleness=routes_staleness,
        graph=graph_state,
        toolchain={str(k): v for k, v in project.config.toolchain.items()},
        generated_policy=generated_policy,
    )


def _check_reason(status: str) -> str:
    """One sentence per compiler-check outcome, for the status payload.

    Display text beside the signal — the verdict itself never comes from here.
    Spelled as comparisons (consuming the vocabulary) rather than as a mapping
    table, so this module never materialises a verdict string of its own.
    """
    if status == "clean":
        return (
            "run_compiler(mode='check') reproduces the committed generated tree and "
            "route index from the current registry."
        )
    if status == "drift":
        return (
            "run_compiler(mode='check') cannot reproduce the committed generated tree "
            "and route index from the current registry — the published content has moved."
        )
    if status == "invalid":
        return "The compiler rejects this registry or cannot compare the generated tree."
    if status == "unverifiable":
        return (
            "The compiler check could not run (typically: no disposable lock directory "
            "in a cold clone), so it proves nothing about the committed bytes either way."
        )
    if status == "never_written":
        return "There is nothing here for the compiler to check yet."
    return f"The compiler check reported unrecognised vocabulary {status!r}."


#: Maps a ``refresh.status`` artifact key to its ``[generated]`` policy key.
#: ``registry`` and ``provider_outputs`` have no policy entry by design — the
#: registry is canonical, and provider outputs follow the ``loadouts`` policy.
_POLICY_KEY = {
    "generated_nodes": "lessons",
    "routes": "routes",
    "graph": "graph",
    "provider_outputs": "loadouts",
}


# ── lessons ─────────────────────────────────────────────────────────────────


def _declared_paths(project: ResolvedProject, lesson: dict[str, Any]) -> list[str]:
    """Expand ``location.file`` using the project's configured separator.

    The compiler owns the authoritative expansion; this reproduces only the
    separator split so the UI can show which paths a lesson claims even when
    the registry has not been compiled yet. Everything downstream that matters —
    routing, delivery — comes from the compiled index instead.
    """
    raw = str((lesson.get("location") or {}).get("file") or "")
    separator = str(project.config.validation.get("location_file_separator") or "")
    values = [raw]
    if separator:
        values = [part.strip() for part in raw.split(separator)]
    return [value for value in values if value]


def _lesson_summary(
    project: ResolvedProject,
    lesson: dict[str, Any],
    *,
    routes_by_node: dict[str, dict[str, list[str]]],
    generated_node_ids: set[str],
) -> LessonSummary:
    lesson_id = str(lesson["id"])
    node_id = currency.lesson_node_id(project, lesson_id)
    declared = _declared_paths(project, lesson)
    routed = routes_by_node.get(node_id, {})
    return LessonSummary(
        id=lesson_id,
        node_id=node_id,
        overlay_node_id=_lesson_node_id(project.repository_id, node_id),
        repository_id=project.repository_id,
        location=LessonLocation(**{k: str(v) for k, v in (lesson.get("location") or {}).items()}),
        found_by=LessonFoundBy(**{k: str(v) for k, v in (lesson.get("found_by") or {}).items()}),
        references=LessonReferences(**{k: str(v) for k, v in (lesson.get("references") or {}).items()}),
        symptom=str(lesson.get("symptom") or ""),
        root_cause=str(lesson.get("root_cause") or ""),
        fix=str(lesson.get("fix") or ""),
        prevention=str(lesson.get("prevention") or ""),
        notes=lesson.get("notes"),
        related=[str(value) for value in (lesson.get("related") or [])],
        date_found=str(lesson.get("date_found") or ""),
        date_found_precision=str(lesson.get("date_found_precision") or ""),
        date_fixed=str(lesson.get("date_fixed") or ""),
        date_fixed_precision=str(lesson.get("date_fixed_precision") or ""),
        declared_paths=declared,
        routed_paths=sorted(routed.get("paths", [])),
        routed_symbols=sorted(routed.get("symbols", [])),
        documentation_only=bool(declared) and all(is_documentation_path(p) for p in declared),
        node_generated=node_id in generated_node_ids,
    )


def _routes_by_node(routes: dict[str, Any] | None) -> dict[str, dict[str, list[str]]]:
    """Invert the compiled route index: node id → the keys that route to it."""
    inverted: dict[str, dict[str, list[str]]] = {}
    for kind, bucket in (("paths", "by_path"), ("symbols", "by_symbol")):
        for key, entries in ((routes or {}).get(bucket) or {}).items():
            for entry in entries or []:
                node_id = str(entry.get("node_id") or "")
                if not node_id:
                    continue
                inverted.setdefault(node_id, {}).setdefault(kind, []).append(str(key))
    return inverted


def _generated_node_ids(project: ResolvedProject) -> set[str]:
    """Node ids present in the generated tree. A read-only directory listing of
    an explicitly configured path — not discovery."""
    try:
        directory = project.path("generated_nodes")
    except FailureMemoryAdapterError:
        return set()
    if not directory.is_dir():
        return set()
    return {path.stem for path in directory.glob(f"{project.node_namespace}-*.md")}


def _matches(value: str, needle: str) -> bool:
    return needle.casefold() in value.casefold()


def build_lessons(
    project: ResolvedProject,
    *,
    path: str | None = None,
    symbol: str | None = None,
    category: str | None = None,
    root_cause: str | None = None,
    related: str | None = None,
    query: str | None = None,
) -> LessonsResponse:
    """Lesson inventory with the plan's five filters plus a free-text query.

    ``category`` matches either ``found_by.type`` (how the failure was
    discovered) or ``location.area``; the registry schema has no single field
    called "category", and both are what an operator means by it. Both value
    sets are enumerated in ``facets.categories``.

    ``related`` matches lessons in EITHER direction — those that declare the
    given lesson id in ``related``, and the lesson itself — because
    ``load_with`` co-activation is what the operator is actually looking for.
    """
    lessons = adapter.registry_lessons(project)
    routes = adapter.route_index(project)
    routes_by_node = _routes_by_node(routes)
    generated = _generated_node_ids(project)
    summaries = [
        _lesson_summary(
            project, lesson, routes_by_node=routes_by_node, generated_node_ids=generated
        )
        for lesson in lessons
    ]

    facets = LessonFacets(
        categories=sorted(
            {s.found_by.type for s in summaries if s.found_by.type}
            | {s.location.area for s in summaries if s.location.area}
        ),
        areas=sorted({s.location.area for s in summaries if s.location.area}),
        packages=sorted({s.location.package for s in summaries if s.location.package}),
        modules=sorted({s.location.module for s in summaries if s.location.module}),
        symbols=sorted({s.location.symbol for s in summaries if s.location.symbol}),
        paths=sorted({p for s in summaries for p in s.declared_paths}),
    )

    filtered = summaries
    if path:
        filtered = [
            s
            for s in filtered
            if any(_matches(p, path) for p in s.declared_paths + s.routed_paths)
        ]
    if symbol:
        filtered = [
            s
            for s in filtered
            if _matches(s.location.symbol, symbol)
            or any(_matches(value, symbol) for value in s.routed_symbols)
        ]
    if category:
        filtered = [
            s
            for s in filtered
            if _matches(s.found_by.type, category) or _matches(s.location.area, category)
        ]
    if root_cause:
        filtered = [s for s in filtered if _matches(s.root_cause, root_cause)]
    if related:
        needle = related.strip()
        filtered = [s for s in filtered if needle in s.related or s.id == needle]
    if query:
        filtered = [
            s
            for s in filtered
            if any(
                _matches(value, query)
                for value in (
                    s.id,
                    s.node_id,
                    s.symptom,
                    s.root_cause,
                    s.fix,
                    s.prevention,
                    s.notes or "",
                    s.location.file,
                    s.location.module,
                    s.location.package,
                    s.location.symbol,
                    s.found_by.trigger,
                )
            )
        ]

    return LessonsResponse(
        project_id=project.id,
        repository_id=project.repository_id,
        total=len(summaries),
        count=len(filtered),
        lessons=filtered,
        facets=facets,
    )


def build_lesson_detail(project: ResolvedProject, lesson_id: str) -> LessonDetail:
    inventory = build_lessons(project)
    by_id = {summary.id: summary for summary in inventory.lessons}
    summary = by_id.get(lesson_id)
    if summary is None:
        raise FailureMemoryAdapterError(
            f"No lesson {lesson_id!r} in project {project.id!r}", code="unknown_lesson"
        )
    routes = adapter.route_index(project)
    entries = [
        entry
        for entry in _route_entries(project, routes)
        if entry.node_id == summary.node_id
    ]
    node_path: str | None = None
    if summary.node_generated:
        node_path = str(
            (project.path("generated_nodes") / f"{summary.node_id}.md").relative_to(project.root)
        )
    return LessonDetail(
        project_id=project.id,
        repository_id=project.repository_id,
        lesson=summary,
        node_path=node_path,
        node_markdown=adapter.generated_node_text(project, summary.node_id),
        related_lessons=[by_id[rid] for rid in summary.related if rid in by_id],
        routes=entries,
    )


# ── routes + overlay ────────────────────────────────────────────────────────


def _route_entries(
    project: ResolvedProject, routes: dict[str, Any] | None
) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    for kind, bucket, node_fn in (
        ("path", "by_path", _file_node_id),
        ("symbol", "by_symbol", _symbol_node_id),
    ):
        for key, records in sorted(((routes or {}).get(bucket) or {}).items()):
            for record in records or []:
                node_id = str(record.get("node_id") or "")
                provenance = record.get("provenance") or {}
                reasons = [
                    RouteReason(
                        source_field=str(reason.get("source_field") or ""),
                        matched_text=str(reason.get("matched_text") or ""),
                    )
                    for reason in (provenance.get("reasons") or [])
                ]
                entries.append(
                    RouteEntry(
                        kind=kind,  # type: ignore[arg-type]
                        key=str(key),
                        node_id=node_id,
                        lesson_id=_lesson_id_from_node(project, node_id),
                        overlay_edge_id=(
                            f"{node_fn(project.repository_id, str(key))}"
                            f"->{_lesson_node_id(project.repository_id, node_id)}"
                        ),
                        match_semantics=provenance.get("match_semantics"),
                        reasons=reasons,
                        source_exists=(
                            (project.root / str(key)).exists() if kind == "path" else None
                        ),
                        documentation=is_documentation_path(str(key)) if kind == "path" else False,
                    )
                )
    return entries


def _lesson_id_from_node(project: ResolvedProject, node_id: str) -> str | None:
    prefix = f"{project.node_namespace}-"
    if not node_id.startswith(prefix):
        return None
    return node_id[len(prefix) :].upper()


def build_overlay(
    project: ResolvedProject,
    *,
    entries: Iterable[RouteEntry],
    lessons: Iterable[LessonSummary],
    selection: dict[str, str] | None = None,
) -> Overlay:
    """Assemble the overlay graph.

    Shape, verbatim from the plan::

        source-file node  --failure_route-->  failure-lesson node
        failure-lesson node --load_with-->    related lesson
        task/diff preview --selected_*-->     failure-lesson node

    ``selection`` maps node id → selection class and, when present, adds the
    ``selected_required`` / ``selected_coactivated`` / ``selected_advisory``
    edges from a synthetic preview node. Every id carries the repository
    prefix, including the preview node's, so two projects previewed side by
    side never share one.
    """
    repository_id = project.repository_id
    nodes: dict[str, OverlayNode] = {}
    edges: list[OverlayEdge] = []
    lesson_list = list(lessons)
    lesson_by_node = {lesson.node_id: lesson for lesson in lesson_list}

    def add_node(node: OverlayNode) -> None:
        nodes.setdefault(node.id, node)

    for lesson in lesson_list:
        add_node(
            OverlayNode(
                id=lesson.overlay_node_id,
                kind="failure_lesson",
                project_id=project.id,
                repository_id=repository_id,
                label=lesson.node_id,
                node_id=lesson.node_id,
                lesson_id=lesson.id,
                documentation=lesson.documentation_only,
                selection_class=(selection or {}).get(lesson.node_id),  # type: ignore[arg-type]
            )
        )

    for entry in entries:
        if entry.kind == "path":
            source_id = _file_node_id(repository_id, entry.key)
            add_node(
                OverlayNode(
                    id=source_id,
                    kind="source_file",
                    project_id=project.id,
                    repository_id=repository_id,
                    label=entry.key,
                    path=entry.key,
                    exists=entry.source_exists,
                    documentation=entry.documentation,
                )
            )
        else:
            source_id = _symbol_node_id(repository_id, entry.key)
            add_node(
                OverlayNode(
                    id=source_id,
                    kind="source_file",
                    project_id=project.id,
                    repository_id=repository_id,
                    label=entry.key,
                )
            )
        target_id = _lesson_node_id(repository_id, entry.node_id)
        add_node(
            OverlayNode(
                id=target_id,
                kind="failure_lesson",
                project_id=project.id,
                repository_id=repository_id,
                label=entry.node_id,
                node_id=entry.node_id,
                lesson_id=entry.lesson_id,
                selection_class=(selection or {}).get(entry.node_id),  # type: ignore[arg-type]
            )
        )
        edges.append(
            OverlayEdge(
                id=entry.overlay_edge_id,
                source=source_id,
                target=target_id,
                type="failure_route",
                project_id=project.id,
                repository_id=repository_id,
                label=entry.match_semantics,
            )
        )

    for lesson in lesson_list:
        for related_id in lesson.related:
            related = lesson_by_node.get(f"{project.node_namespace}-{related_id.lower()}")
            if related is None:
                continue
            edges.append(
                OverlayEdge(
                    id=f"{lesson.overlay_node_id}->load_with->{related.overlay_node_id}",
                    source=lesson.overlay_node_id,
                    target=related.overlay_node_id,
                    type="load_with",
                    project_id=project.id,
                    repository_id=repository_id,
                    label="load_with",
                )
            )

    if selection:
        preview_id = overlay_node_id(repository_id, "preview", project.id)
        add_node(
            OverlayNode(
                id=preview_id,
                kind="source_file",
                project_id=project.id,
                repository_id=repository_id,
                label="task/diff preview",
            )
        )
        for node_id, selection_class in sorted(selection.items()):
            target_id = _lesson_node_id(repository_id, node_id)
            add_node(
                OverlayNode(
                    id=target_id,
                    kind="failure_lesson",
                    project_id=project.id,
                    repository_id=repository_id,
                    label=node_id,
                    node_id=node_id,
                    lesson_id=_lesson_id_from_node(project, node_id),
                    selection_class=selection_class,  # type: ignore[arg-type]
                )
            )
            edges.append(
                OverlayEdge(
                    id=f"{preview_id}->selected_{selection_class}->{target_id}",
                    source=preview_id,
                    target=target_id,
                    type=f"selected_{selection_class}",  # type: ignore[arg-type]
                    project_id=project.id,
                    repository_id=repository_id,
                    label=selection_class,
                )
            )

    return Overlay(
        project_id=project.id,
        repository_id=repository_id,
        nodes=list(nodes.values()),
        edges=edges,
    )


def build_routes(project: ResolvedProject) -> RoutesResponse:
    routes = adapter.route_index(project)
    entries = _route_entries(project, routes)
    inventory = build_lessons(project)
    status = adapter.artifact_status(project)
    registry_sha256 = (status.get("artifacts") or {}).get("registry", {}).get("sha256")

    routed_nodes = {entry.node_id for entry in entries}
    path_entries = [entry for entry in entries if entry.kind == "path"]
    coverage = RouteCoverage(
        lesson_count=len(inventory.lessons),
        routed_lesson_count=sum(1 for l in inventory.lessons if l.node_id in routed_nodes),
        unrouted_lesson_ids=[l.id for l in inventory.lessons if l.node_id not in routed_nodes],
        path_route_count=len(path_entries),
        symbol_route_count=sum(1 for entry in entries if entry.kind == "symbol"),
        missing_source_paths=sorted(
            {entry.key for entry in path_entries if entry.source_exists is False}
        ),
        documentation_path_count=len({entry.key for entry in path_entries if entry.documentation}),
    )

    return RoutesResponse(
        project_id=project.id,
        repository_id=project.repository_id,
        schema_version=(routes or {}).get("schema_version"),
        source_hash=(routes or {}).get("source_hash"),
        compiled=routes is not None,
        staleness=currency.route_index_staleness(registry_sha256, routes),
        routes=entries,
        coverage=coverage,
        overlay=build_overlay(project, entries=entries, lessons=inventory.lessons),
    )


# ── resolve preview ─────────────────────────────────────────────────────────


def build_provider_request(
    project: ResolvedProject, payload: ResolveRequest
) -> dict[str, Any]:
    """Compose a ``failure-memory-provider-request/v1`` object.

    Three fields are service-owned rather than caller-owned, deliberately:

    * ``repository_id`` comes from the project's validated config, so a preview
      cannot be aimed at one project while claiming another's identity (the
      package rejects a mismatch with ``provider_identity`` anyway; this makes
      it impossible rather than merely caught).
    * ``output_dir`` is the configured provider-output root. It is never taken
      from the request — a caller-supplied output directory is precisely the
      shape of an arbitrary-write bug, and read-only preview passes
      ``write_artifacts=False`` so nothing is created there regardless.
    * ``refresh_policy`` is the literal ``"never"``. ``require-current`` runs a
      fail-closed toolchain preflight (a pinned editable repo2md checkout, an
      explicit read-only global vault) under the project lock — appropriate for
      a dispatch gate, not for an inspection UI that must work on a machine
      that merely has the repository checked out. Logic-Loom reports staleness
      instead of asserting it. A literal rather than a module constant, for the
      same reason ``write_artifacts`` is a literal in the adapter: a module
      constant is a runtime rebind away from changing what the surface does.

    ``task.symbols`` is emitted ONLY when the caller supplied symbols. Always
    emitting the key (even as ``[]``) would add a field to the canonical task
    object, and the task object feeds ``result.fingerprint`` — an empty list
    would silently change every fingerprint away from the frozen contract's.
    """
    task: dict[str, Any] = {
        "task_id": payload.task_id,
        "title": payload.title,
        "objective": payload.objective,
        "scope": [],
        "deliverables": [],
        "akms_tags": list(payload.akms_tags),
    }
    if payload.symbols:
        task["symbols"] = list(payload.symbols)
    return {
        "schema_version": _REQUEST_SCHEMA_VERSION,
        "invocation_id": payload.invocation_id,
        "repository_id": project.repository_id,
        "baseline": payload.baseline,
        "mode": payload.mode,
        "role": payload.role,
        "declared_paths": list(payload.declared_paths),
        "changed_paths": list(payload.changed_paths),
        "base": payload.base,
        "head": payload.head,
        "refresh_policy": "never",
        "output_dir": str(project.config.paths["provider_outputs"]),
        "task": task,
    }


def _record_views(
    project: ResolvedProject, records: Iterable[dict[str, Any]], selection_class: str
) -> list[RecordView]:
    views = []
    for record in records:
        if record.get("selection_class") != selection_class:
            continue
        node_id = str(record.get("node_id") or "")
        views.append(
            RecordView(
                node_id=node_id,
                lesson_id=_lesson_id_from_node(project, node_id),
                overlay_node_id=_lesson_node_id(project.repository_id, node_id),
                selection_class=selection_class,  # type: ignore[arg-type]
                content_ref=record.get("content_ref"),
                source_ref=record.get("source_ref"),
                reasons=[str(reason) for reason in (record.get("reasons") or [])],
            )
        )
    return views


def _route_keys(bucket: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Route entries for one scope key, honouring AKMS's glob scope inputs.

    An exact key is a dict lookup. A key carrying glob metacharacters is a glob
    input as far as AKMS is concerned, so it is matched against every route key
    — otherwise a task scoped as ``src/engine/*`` would produce records with an
    empty candidate set, and the verdict would contradict them.
    """
    exact = bucket.get(key)
    if exact:
        return list(exact)
    if not any(character in key for character in "*?["):
        return []
    matched: list[dict[str, Any]] = []
    for candidate_key, entries in bucket.items():
        if fnmatch(candidate_key, key):
            matched.extend(entries or [])
    return matched


def _candidate_node_ids(
    routes: dict[str, Any] | None, *, paths: Iterable[str], symbols: Iterable[str]
) -> list[str]:
    """Lesson nodes with a live compiled route into this task, in order.

    Both buckets are consulted. ``by_symbol`` is usually empty — the compiler
    emits a symbol route only when the referenced file exists in the checkout —
    but "usually empty" is not "never populated", and a symbol-routed delivery
    with a path-only candidate set is exactly the shape of a verdict that
    contradicts its own records.
    """
    ordered: list[str] = []
    for bucket_name, keys in (("by_path", paths), ("by_symbol", symbols)):
        bucket = (routes or {}).get(bucket_name) or {}
        for key in keys:
            for entry in _route_keys(bucket, key):
                node_id = str(entry.get("node_id") or "")
                if node_id and node_id not in ordered:
                    ordered.append(node_id)
    return ordered


def _delivery_report(
    *,
    request_paths: list[str],
    provider_paths: list[str],
    symbols: list[str],
    routes: dict[str, Any] | None,
    records: list[dict[str, Any]],
    resolution: dict[str, Any],
) -> DeliveryReport:
    """Decide, from evidence, why the required lane looks the way it does.

    The candidate set is the COMPILED ROUTE INDEX intersected with every path
    and symbol the resolution actually considered; the delivered set is the
    provider's own records. The gap between them is the point: a lesson can
    have a live route, the call can return ``status: "ok"``, and the lesson
    can still be delivered nowhere at all.

    The considered set is not the requested set: in a ``post-diff`` resolution
    the effective paths come from the provider's git diff and appear only in
    ``resolution.changed_paths``, so attribution must include them. The branch
    order below keeps the verdict consistent by construction: every candidate
    is either delivered or in ``nowhere``, so ``no_lessons_apply`` requires
    both an empty candidate set AND an empty record set.
    """
    seen: set[str] = set()
    input_paths = [
        path
        for path in request_paths + provider_paths
        if path and not (path in seen or seen.add(path))
    ]
    candidates = _candidate_node_ids(routes, paths=input_paths, symbols=symbols)

    delivered = [str(record.get("node_id") or "") for record in records]
    by_class = {
        str(record.get("node_id") or ""): str(record.get("selection_class") or "")
        for record in records
    }
    required = [node for node, cls in by_class.items() if cls == "required"]
    nowhere = [node for node in candidates if node not in by_class]
    downgraded = [node for node in candidates if by_class.get(node) == "advisory"]
    unrouted = [node for node in delivered if node not in candidates]
    advisory_only = [node for node in unrouted if by_class.get(node) == "advisory"]
    coactivated_only = [node for node in unrouted if by_class.get(node) == "coactivated"]

    documentation = [p for p in input_paths if is_documentation_path(p)]
    code = [p for p in input_paths if not is_documentation_path(p)]
    documentation_only = bool(input_paths) and not code
    empty_diff_fallback = bool(resolution.get("empty_diff_fallback"))

    if nowhere:
        verdict = "delivered_nowhere"
        headline = (
            f"{len(nowhere)} lesson(s) with a live compiled route were delivered NOWHERE — "
            "not required, not coactivated, not advisory. The route exists; delivery was "
            "suppressed. This is not an empty result."
        )
    elif downgraded:
        verdict = "downgraded_to_advisory"
        headline = (
            f"{len(downgraded)} routed lesson(s) arrived only as advisory, not required."
        )
    elif delivered:
        # ANY delivered record means something reached the caller. Reporting
        # "no lessons apply" here — as this did for advisory-tag and load_with
        # arrivals — contradicts the records sitting in the same response.
        verdict = "delivered"
        routed = len(candidates)
        headline = (
            f"{len(delivered)} lesson(s) were delivered"
            + (f", {routed} of them through a compiled route." if routed else ".")
        )
    else:
        verdict = "no_lessons_apply"
        headline = (
            "No compiled route matches any considered path or symbol, and nothing was "
            "delivered — there is genuinely nothing to deliver."
        )

    if unrouted:
        # Records that arrived without a matching compiled route came in some
        # other way, and the ways are not the same thing: an advisory record
        # matched an akms_tag, a coactivated record was pulled in by another
        # lesson's `load_with`. Attributing a co-activation to tag matching
        # would be its own small lie.
        if advisory_only:
            headline += (
                f" {len(advisory_only)} lesson(s) arrived through advisory tag matching "
                "rather than through a compiled route."
            )
        if coactivated_only:
            headline += (
                f" {len(coactivated_only)} lesson(s) arrived through load_with "
                "co-activation rather than through a compiled route of their own."
            )
        other = [
            node
            for node in unrouted
            if node not in advisory_only and node not in coactivated_only
        ]
        if other:
            headline += (
                f" {len(other)} lesson(s) arrived without a matching compiled route."
            )

    if provider_paths:
        headline += (
            f" {len(provider_paths)} path(s) came from the provider's own diff resolution, "
            "not from the request."
        )
    if empty_diff_fallback:
        headline += (
            " The diff resolved to no paths, so AKMS fell back to the task's declared "
            "scope; this post-diff answer is really a pre-task answer."
        )

    if documentation_only and verdict in {"delivered_nowhere", "downgraded_to_advisory"}:
        headline += (
            " Every considered path classifies as documentation, so AKMS dropped the "
            "exact-path specs; adding one non-documentation path to the same task restores "
            "the same lesson to required."
        )

    return DeliveryReport(
        verdict=verdict,  # type: ignore[arg-type]
        headline=headline,
        input_paths=input_paths,
        request_paths=request_paths,
        provider_derived_paths=provider_paths,
        symbols=symbols,
        empty_diff_fallback=empty_diff_fallback,
        documentation_paths=documentation,
        code_paths=code,
        documentation_only=documentation_only,
        routed_candidate_node_ids=candidates,
        delivered_node_ids=delivered,
        required_node_ids=required,
        delivered_nowhere_node_ids=nowhere,
        downgraded_node_ids=downgraded,
        tag_only_node_ids=unrouted,
    )


def build_resolve(project: ResolvedProject, payload: ResolveRequest) -> ResolveResponse:
    request = build_provider_request(project, payload)
    result = adapter.resolve_preview(project, request)
    records = list(result.get("records") or [])
    resolution = dict(result.get("resolution") or {})
    routes = adapter.route_index(project)
    inventory = build_lessons(project)

    request_paths = _dedupe(list(payload.declared_paths) + list(payload.changed_paths))
    # `resolution.changed_paths` is the provider's EFFECTIVE path set. For a
    # post-diff call given base/head it is the git-derived diff and appears
    # nowhere in the request; for an empty diff it is AKMS's fallback to the
    # declared scope. Either way it is the set the resolution actually used, so
    # attribution has to include it or the verdict can contradict the records.
    provider_paths = [
        path
        for path in _dedupe(
            [str(value) for value in (resolution.get("changed_paths") or [])]
        )
        if path not in request_paths
    ]
    symbols = _dedupe(list(payload.symbols))

    delivery = _delivery_report(
        request_paths=request_paths,
        provider_paths=provider_paths,
        symbols=symbols,
        routes=routes,
        records=records,
        resolution=resolution,
    )

    selection = {
        str(record.get("node_id") or ""): str(record.get("selection_class") or "")
        for record in records
    }
    considered = set(delivery.input_paths)
    entries = [
        entry
        for entry in _route_entries(project, routes)
        if (entry.kind == "path" and entry.key in considered)
        or (entry.kind == "symbol" and entry.key in set(symbols))
    ]

    return ResolveResponse(
        project_id=project.id,
        repository_id=project.repository_id,
        write_artifacts=False,
        request=request,
        result=result,
        required=_record_views(project, records, "required"),
        coactivated=_record_views(project, records, "coactivated"),
        advisory=_record_views(project, records, "advisory"),
        delivery=delivery,
        overlay=build_overlay(
            project,
            entries=entries,
            lessons=[l for l in inventory.lessons if l.node_id in selection],
            selection=selection,
        ),
    )


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    return [
        value for value in values if value and not (value in seen or seen.add(value))
    ]


# ── check ───────────────────────────────────────────────────────────────────


def build_check(settings: Settings, project: ResolvedProject) -> CheckResponse:
    """Run the compiler's two non-writing modes and report the diagnostics."""
    global_vault: Path = settings.effective_akms_global_vault().expanduser()
    vault_exists = global_vault.is_dir()

    # This endpoint IS the diagnostic, so a registry the compiler rejects is a
    # result to report, not an error to raise: a 412 with a stack-trace-shaped
    # message tells an operator far less than `status: "invalid"` plus the
    # compiler's own code and message.
    try:
        validation = adapter.validate_registry(project)
    except FailureMemoryAdapterError as exc:
        logger.info("failure-memory validate failed for %s", project.id, exc_info=True)
        # `lock_parent_missing` is not a fault, and not proof the project is
        # untouched either: a genuinely fresh project reads `never_written`,
        # while an ordinary cold clone (artifacts committed, scratch tree not
        # materialised) reads `unverifiable` — the check could not run, so it
        # proves nothing about the committed bytes. `GET /status` still
        # reports real staleness here, because detecting it needs no lock.
        return CheckResponse(
            project_id=project.id,
            repository_id=project.repository_id,
            status=currency.lock_blocked_check_status(project, exc.code),  # type: ignore[arg-type]
            drift=False,
            validation={"status": "invalid", "code": exc.code, "message": str(exc)},
            check=None,
            global_vault=str(global_vault),
            global_vault_exists=vault_exists,
        )

    try:
        check = adapter.check_generated(project, global_vault=global_vault)
    except FailureMemoryAdapterError as exc:
        # The registry is well-formed but the generated tree could not be
        # compared — a node-id collision against the global vault, or a
        # configured path that escapes the repository. Never a green tick.
        logger.info("failure-memory check failed for %s", project.id, exc_info=True)
        return CheckResponse(
            project_id=project.id,
            repository_id=project.repository_id,
            status=currency.lock_blocked_check_status(project, exc.code),  # type: ignore[arg-type]
            drift=False,
            validation=validation,
            check={"status": "error", "code": exc.code, "message": str(exc)},
            global_vault=str(global_vault),
            global_vault_exists=vault_exists,
        )

    drift = check.get("status") == "drift"
    # Fail closed on unknown compiler vocabulary: only the compiler's own
    # literal "clean" may read as clean. Anything unrecognised is a state this
    # surface cannot vouch for, and mapping it to "clean" would be one more
    # gate reporting fail-closed while not being so.
    status = "drift" if drift else ("clean" if check.get("status") == "clean" else "unverifiable")
    return CheckResponse(
        project_id=project.id,
        repository_id=project.repository_id,
        status=status,  # type: ignore[arg-type]
        drift=drift,
        validation=validation,
        check=check,
        global_vault=str(global_vault),
        global_vault_exists=vault_exists,
        warnings=[
            {str(k): str(v) for k, v in warning.items()}
            for warning in (check.get("warnings") or [])
        ],
    )
