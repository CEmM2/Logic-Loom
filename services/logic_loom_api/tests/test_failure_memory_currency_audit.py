"""Behavioural checks on the currency owner and the job registry.

``services/failure_memory_currency.py`` is the single module that decides
"has this project been written to?" and "is it current?" — everything else
forwards its answers. These tests pin the properties that keep that true:

* one definition of ``write_history``, and a verdict function that cannot be
  called with a signal missing;
* verdicts derived from CONTENT (the graph document's real nodes, the
  published generated files), never from the disposable lock/runtime tree —
  asserted by executing the code against staged states rather than by
  inspecting its source, so ordinary refactoring stays cheap;
* job lifecycle: outcomes only from the package, atomic admission, a
  recency-capped history that is never trimmed from the publication path,
  and a journal that survives restarts.

The end-to-end versions of the currency properties — cold clones, corrupted
artifacts, verbatim forwarding of owner verdicts over HTTP — live in
``test_failure_memory_operator.py``; these are the fast unit-level halves.
"""

from __future__ import annotations

import ast
import inspect
import json
import threading
from pathlib import Path
from typing import Any, Iterator

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parents[1] / "src" / "logic_loom_api"

#: The one module allowed to compute these verdicts.
OWNER = "services/failure_memory_currency.py"

#: Everything else in the feature.
CONSUMERS = (
    "adapters/failure_memory.py",
    "adapters/failure_memory_operator.py",
    "services/failure_memory.py",
    "services/failure_memory_operator.py",
    "services/failure_memory_jobs.py",
    "routes/failure_memory.py",
    "models/failure_memory.py",
)

def _tree(relative: str) -> ast.Module:
    return ast.parse((_SRC / relative).read_text(encoding="utf-8"), filename=relative)


# ── the owner is singular and cannot be underfed ────────────────────────────


def test_write_history_is_defined_exactly_once() -> None:
    definitions = []
    for relative in (OWNER, *CONSUMERS):
        for node in ast.walk(_tree(relative)):
            if isinstance(node, ast.FunctionDef) and node.name in {
                "write_history",
                "never_written",
            }:
                definitions.append(f"{relative}:{node.name}")
    assert definitions == [f"{OWNER}:write_history"], definitions


def test_resolve_currency_cannot_be_called_with_an_incomplete_signal_set() -> None:
    """``check_status`` and ``generated`` are REQUIRED arguments, no defaults.

    A signature default is an invitation to omit a signal silently; a
    ``TypeError`` is not. Omitting the check evidence once let corrupted
    generated content read as ``current``.
    """
    from logic_loom_api.services.failure_memory_currency import resolve_currency

    signature = inspect.signature(resolve_currency)
    for name in ("history", "routes_state", "graph", "check_status", "generated"):
        parameter = signature.parameters[name]
        assert parameter.default is inspect.Parameter.empty, (
            f"resolve_currency({name}=...) has a default; every signal must be "
            "explicitly supplied so omission is a TypeError, not a silent launder"
        )


# ── behavioural probes: graph verification reads CONTENT ────────────────────


class _StubProject:
    """Duck-typed ResolvedProject over a scratch directory."""

    def __init__(self, root: Path, repository_id: str = "Stub", node_namespace: str = "stub-failure") -> None:
        self.root = root
        self.repository_id = repository_id
        self.node_namespace = node_namespace

    def path(self, key: str) -> Path:
        return {
            "registry": self.root / "lessons.json",
            "routes": self.root / "generated" / "routes.json",
            "graph": self.root / "runtime" / "graph.json",
            "generated_nodes": self.root / "generated" / "nodes",
            "lock": self.root / "runtime" / "lock",
        }[key]


def _routes_for(node_ids: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "failure-memory-routes/v1",
        "source_hash": "sha256:" + "0" * 64,
        "by_path": {"src/app.py": [{"node_id": node_id} for node_id in node_ids]},
        "by_symbol": {},
    }


def _write_graph(
    project: _StubProject,
    *,
    header: dict[str, Any],
    node_ids: list[str],
    load_with: dict[str, list[str]] | None = None,
) -> None:
    graph_path = project.path("graph")
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(
            {
                "graph": header,
                "nodes": [
                    {"id": node_id, "load_with": (load_with or {}).get(node_id, [])}
                    for node_id in node_ids
                ],
                "links": [],
            }
        ),
        encoding="utf-8",
    )


def test_graph_verification_actually_uses_the_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verification must read the graph's CONTENT, not its metadata header.

    A zero-node document whose header still claims seven nodes, and a document
    missing one routed node, must both be refused; agreement must verify. The
    published-artifact agreement question itself (identity, graph nodes vs
    generated files, shared fields, load_with targets) is owned by the
    package's ``provider.validate_publication`` — the single definition every
    consumer inherits — so here it is stubbed at the adapter seam: any problem
    it reports must surface as ``content_mismatch``/stale, and the end-to-end
    cases live in ``test_failure_memory_operator.py`` against the real package.
    """
    from logic_loom_api.adapters import failure_memory as adapter_module
    from logic_loom_api.services.failure_memory_currency import verify_graph_content

    project = _StubProject(tmp_path)
    routed = ["stub-failure-l001", "stub-failure-l002"]
    monkeypatch.setattr(adapter_module, "publication_problems", lambda _p: [])
    header = {
        "generated_at": "2001-01-01T00:00:00+00:00",
        "repo_id": "Stub",
        "node_count": 7,
        "edge_count": 3,
    }

    _write_graph(project, header=header, node_ids=[])
    forged = verify_graph_content(
        project,
        graph_metadata=header,
        routes=_routes_for(routed),
        derived_generated_at="2001-01-01T00:00:00+00:00",
        routes_state="current",
    )
    assert not forged.verified, "a zero-node graph with a lying header was verified"
    assert forged.label == "stale"
    assert forged.graph_node_count == 0
    assert set(forged.missing_node_ids) == set(routed)

    _write_graph(project, header=header, node_ids=routed[:1])
    partial = verify_graph_content(
        project,
        graph_metadata=header,
        routes=_routes_for(routed),
        derived_generated_at="2001-01-01T00:00:00+00:00",
        routes_state="current",
    )
    assert not partial.verified and partial.state == "missing_nodes"
    assert partial.missing_node_ids == [routed[1]]

    _write_graph(project, header=header, node_ids=routed)
    complete = verify_graph_content(
        project,
        graph_metadata=header,
        routes=_routes_for(routed),
        derived_generated_at="2001-01-01T00:00:00+00:00",
        routes_state="current",
    )
    assert complete.verified, "the honest graph must still verify"

    # Any problem the package's publication validation reports is definitive:
    # content_mismatch, label stale, the package's own words in the reason.
    monkeypatch.setattr(
        adapter_module,
        "publication_problems",
        lambda _p: ["graph node stub-failure-l999 has no published generated node file behind it"],
    )
    flagged = verify_graph_content(
        project,
        graph_metadata=header,
        routes=_routes_for(routed),
        derived_generated_at="2001-01-01T00:00:00+00:00",
        routes_state="current",
    )
    assert flagged.state == "content_mismatch" and flagged.label == "stale"
    assert "stub-failure-l999" in flagged.reason


def test_check_evidence_is_load_bearing_in_the_verdict() -> None:
    """Drift blocks. Unobtainable blocks. Unknown vocabulary blocks. Clean is
    required — not merely permitted — for ``current``."""
    from logic_loom_api.services.failure_memory_currency import (
        GeneratedCoverage,
        GraphVerification,
        WriteHistory,
        resolve_currency,
    )

    written = WriteHistory(never_written=False, evidence={"registry": True}, reason="")
    verified = GraphVerification(state="verified", reason="ok", graph_node_count=3)
    covered = GeneratedCoverage(state="complete", reason="")

    def verdict(check_status: str | None) -> str:
        state, _ = resolve_currency(
            history=written,
            routes_state="current",
            graph=verified,
            check_status=check_status,
            generated=covered,
        )
        return state

    assert verdict("clean") == "current"
    assert verdict("drift") == "stale", "compiler drift must be stale, not current"
    assert verdict(None) == "unverifiable", "unobtainable evidence must block"
    assert verdict("unverifiable") == "unverifiable"
    assert verdict("greenish") == "unverifiable", "unknown vocabulary must fail closed"

    incomplete = GeneratedCoverage(
        state="incomplete", reason="missing node files", missing_node_ids=["stub-failure-l001"]
    )
    state, _ = resolve_currency(
        history=written,
        routes_state="current",
        graph=verified,
        check_status="clean",
        generated=incomplete,
    )
    assert state == "stale", "a torn generated tree must be stale even beside a clean check"


def test_write_history_is_invariant_under_the_lock_directory(tmp_path: Path) -> None:
    """Toggling the disposable lock directory must change NO verdict output.

    The lock/runtime tree is scratch — absent in every fresh clone — so its
    presence is not evidence about content or history.
    """
    from logic_loom_api.services import failure_memory_currency as currency

    project = _StubProject(tmp_path)
    project.path("registry").write_text('{"lessons": []}', encoding="utf-8")
    routes_path = project.path("routes")
    routes_path.parent.mkdir(parents=True, exist_ok=True)
    routes_path.write_text(json.dumps(_routes_for([])), encoding="utf-8")

    def observe() -> tuple[Any, ...]:
        history = currency.write_history(project)
        coverage = currency.verify_generated_coverage(project, [])
        state, reason = currency.resolve_currency(
            history=history,
            routes_state="stale",
            graph=currency.GraphVerification(state="absent", reason=""),
            check_status="unverifiable",
            generated=coverage,
        )
        return (history.never_written, dict(history.evidence), coverage.state, state, reason)

    without_lock = observe()
    lock_parent = project.path("lock").parent
    lock_parent.mkdir(parents=True, exist_ok=True)
    with_lock = observe()
    assert without_lock == with_lock, (
        "a verdict changed when ONLY the disposable lock directory changed"
    )
    assert with_lock[3] == "stale"


# ── behavioural probes: job retention, delivery and admission ───────────────


@pytest.fixture
def instrumented_reap() -> Iterator[list[str]]:
    """Record the THREAD each ``_reap`` call runs on (class-level patch)."""
    from logic_loom_api.services.failure_memory_jobs import JobRegistry

    calls: list[str] = []
    original = JobRegistry._reap

    def recording(self: Any) -> None:
        calls.append(threading.current_thread().name)
        original(self)

    JobRegistry._reap = recording  # type: ignore[method-assign]
    try:
        yield calls
    finally:
        JobRegistry._reap = original  # type: ignore[method-assign]


def _work_for(kind: str):
    """A callable reaching the requested terminal state. Retention properties
    hold for failed evidence exactly as for succeeded evidence."""
    if kind == "succeeded":
        return lambda: {"ok": True}

    def failing() -> dict[str, object]:
        raise RuntimeError("deliberate failure — failed evidence is evidence")

    return failing


@pytest.mark.parametrize("kind", ("succeeded", "failed"))
def test_terminal_publication_never_reaps_by_any_spelling(
    instrumented_reap: list[str], kind: str
) -> None:
    """No ``_reap`` call may run on the worker thread.

    Terminal states are published on the worker thread, so this is "retention
    never runs inside terminal publication" as a runtime fact: reaping there
    once let a completed job vanish before a concurrent poller could read it.
    """
    from logic_loom_api.services.failure_memory_jobs import JobRegistry

    registry = JobRegistry(max_retained=1, max_inflight=100)
    for index in range(5):
        job = registry.submit(
            project_id="p",
            action="compile",
            detail=str(index),
            work=_work_for(kind),
            summarize=lambda _r: "done",
        )
        settled = registry.wait(job.id, timeout=30)  # delivers → keeps backlog clear
        assert settled is not None and settled.terminal
        assert settled.status == kind
    assert calls_only_on_submitter_threads(instrumented_reap), (
        f"_reap ran on a worker thread: {instrumented_reap}"
    )


def calls_only_on_submitter_threads(calls: list[str]) -> bool:
    return all(not name.startswith("fm-job") for name in calls)


def test_retention_is_recency_only_never_time_and_never_nonterminal() -> None:
    """Retention is a simple recency cap over terminal jobs. Nothing cleverer.

    No time-based eviction; a nonterminal job is never evicted however far
    over the cap the registry is (it may still write); the trim happens at
    submission, oldest terminal first — both terminal kinds alike.
    """
    from logic_loom_api.services import failure_memory_jobs
    from logic_loom_api.services.failure_memory_jobs import JobRegistry

    source = inspect.getsource(failure_memory_jobs)
    assert "RETENTION_GRACE" not in source, "a grace period crept back in"
    assert "monotonic" not in source, "time-based retention logic crept back in"

    registry = JobRegistry(max_retained=2, max_inflight=100)
    gate = threading.Event()
    blocked = [
        registry.submit(
            project_id="p", action="compile", detail=f"blocked-{index}",
            work=lambda: {"ok": gate.wait(30)}, summarize=lambda _r: "done",
        )
        for index in range(5)
    ]
    # Five nonterminal jobs over a cap of two: none may be trimmed.
    with registry._guard:
        registry._reap()
        assert all(job.id in registry._jobs for job in blocked), (
            "a nonterminal job was reaped"
        )

    gate.set()
    for job in blocked:
        settled = registry.wait(job.id, timeout=30)
        assert settled is not None and settled.terminal

    # A mix of terminal kinds; the next submission trims oldest-first to cap.
    failed_job = registry.submit(
        project_id="p", action="compile", detail="failing",
        work=_work_for("failed"), summarize=lambda _r: "done",
    )
    settled = registry.wait(failed_job.id, timeout=30)
    assert settled is not None and settled.status == "failed"
    registry.submit(
        project_id="p", action="compile", detail="trigger",
        work=lambda: {"ok": True}, summarize=lambda _r: "done",
    )
    with registry._guard:
        assert len(registry._jobs) <= registry._max_retained + 1, (
            "the recency cap did not trim terminal history"
        )


def test_admission_is_atomic_under_concurrency() -> None:
    """8 simultaneous submitters against a limit of 1: exactly one wins.

    The capacity check and the insertion must share one lock acquisition, or
    concurrent submitters all pass the check before any of them inserts.
    """
    from logic_loom_api.services.failure_memory_jobs import (
        JobAdmissionError,
        JobRegistry,
    )

    registry = JobRegistry(max_inflight=1, max_retained=100)
    gate = threading.Event()
    start = threading.Barrier(8)
    admitted: list[str] = []
    rejected: list[JobAdmissionError] = []
    lock = threading.Lock()

    def submit(index: int) -> None:
        start.wait(timeout=10)
        try:
            job = registry.submit(
                project_id="p", action="compile", detail=str(index),
                work=lambda: {"ok": gate.wait(20)}, summarize=lambda _r: "done",
            )
        except JobAdmissionError as exc:
            with lock:
                rejected.append(exc)
        else:
            with lock:
                admitted.append(job.id)

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(admitted) == 1, f"{len(admitted)} concurrent jobs admitted with a limit of 1"
    assert len(rejected) == 7
    assert registry.inflight() == 1
    gate.set()
    settled = registry.wait(admitted[0], timeout=30)
    assert settled is not None and settled.status == "succeeded"


def test_terminal_evidence_survives_a_restart(tmp_path: Path) -> None:
    """The journal: results and interruptions survive an ordinary restart.

    A sidecar restart used to erase every in-memory job — a completed write's
    evidence, and any 202 whose job never finished, both became 404s
    indistinguishable from "never ran". The journal is best effort (a write
    failure is logged and costs restart durability only), which is the right
    trade for a local, single-operator tool.
    """
    from logic_loom_api.services.failure_memory_jobs import Job, JobRegistry

    journal = tmp_path / "jobs.json"
    registry = JobRegistry(journal_path=journal)
    done = registry.submit(
        project_id="p", action="record", detail="real",
        work=lambda: {"status": "created", "record": {"id": "L001"}},
        summarize=lambda _r: "recorded",
    )
    settled = registry.wait(done.id, timeout=30)
    assert settled is not None and settled.status == "succeeded"

    # Simulate a job caught mid-flight by writing its admitted state the way
    # the registry journals it before the worker finishes.
    with registry._guard:
        interrupted = Job(
            id="deadbeef", project_id="p", action="compile", detail="mid-flight",
            status="running", created_at="2026-08-16T00:00:00+00:00",
        )
        registry._jobs[interrupted.id] = interrupted
        registry._persist()

    failed = registry.submit(
        project_id="p", action="compile", detail="real-failure",
        work=_work_for("failed"), summarize=lambda _r: "done",
    )
    settled_failed = registry.wait(failed.id, timeout=30)
    assert settled_failed is not None and settled_failed.status == "failed"

    reborn = JobRegistry(journal_path=journal)
    recovered = reborn.get(done.id)
    assert recovered is not None, "terminal evidence was lost across restart"
    assert recovered.status == "succeeded"
    assert recovered.result == {"status": "created", "record": {"id": "L001"}}
    recovered_failed = reborn.get(failed.id)
    assert recovered_failed is not None, "FAILED evidence was lost across restart"
    assert recovered_failed.status == "failed"
    assert recovered_failed.error is not None

    orphan = reborn.get("deadbeef")
    assert orphan is not None, "an interrupted job vanished across restart"
    assert orphan.status == "failed"
    assert orphan.error is not None and orphan.error.code == "interrupted_by_restart"
    assert "unknown" in orphan.error.message
