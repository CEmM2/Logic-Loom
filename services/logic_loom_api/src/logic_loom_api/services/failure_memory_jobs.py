"""Background jobs for failure-memory write operations.

Recording, compiling and refreshing all take the project lock, and a refresh
additionally shells out to a toolchain preflight, so none of them belong inline
in a request. They run here, on a single worker thread, and the route layer
returns a job id.

The hazard this module prevents is a partially accepted UI state — a write that
looks accepted while it later failed, or one that landed with nothing
observable to show for it. Five properties close it:

1. **Terminal state comes only from the package.** A job is ``succeeded`` iff
   the callable RETURNED; any exception makes it ``failed``. Nothing marks
   success on dispatch or on "no exception so far".
2. **Success requires validated evidence.** A callable returning ``None``, a
   non-mapping or an empty mapping is published as ``failed`` with
   ``missing_result_evidence`` — "did not raise" is not "produced a result".
3. **``result`` and ``succeeded`` are published together**, under one lock, so
   no observer can see a succeeded job without its result.
4. **Retention is a recency cap over TERMINAL jobs and never runs inside
   terminal publication.** Nonterminal jobs are never evicted (they may still
   write), there is no time-based eviction, and reaping happens only at
   submission — reaping inside publication once let a completed job vanish
   before a concurrent poller could read it.
5. **Admission is atomic.** Capacity check and insertion share one lock
   acquisition, so concurrent submitters cannot exceed the limit; an
   over-capacity submission is rejected before anything exists.

Jobs are journalled (best effort, atomic replace + fsync) to a Logic-Loom-owned
file under its cache directory and reloaded on startup: results survive an
ordinary restart, and a job caught mid-flight is republished as ``failed`` /
``interrupted_by_restart`` so its submitter learns the truth rather than
meeting a 404. A journal write failure is logged and costs restart durability
only — this is a local developer tool, not a transaction log.

The single worker is deliberate: the package serialises writes on its own
project lock anyway, so concurrency would only buy ``lock_contention``.

Worth knowing before designing UI on top: every generated node embeds the
whole-registry digest, so one recorded lesson rewrites EVERY generated node —
``affected_outputs`` naming the entire tree is correct output, not damage, and
the job summary says so in words.
"""

from __future__ import annotations

import json
import logging
import os
import atexit
import functools
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config import Settings

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "succeeded", "failed"]
JobAction = Literal["record", "compile", "refresh"]

#: Recency cap on retained TERMINAL jobs. Durable history of the writes
#: themselves lives in the repository's own git history, where the recordings
#: actually land. Terminal jobs only — a queued or running job may still
#: write and is never evicted. See :meth:`JobRegistry._reap`.
MAX_RETAINED_JOBS = 100

#: Hard cap on jobs that are QUEUED OR RUNNING at once, enforced atomically at
#: admission. An over-capacity submission is REJECTED at the API boundary —
#: loudly, before any work is accepted. Accepted work is never silently
#: dropped, so a caller that gets a 202 can rely on it.
MAX_INFLIGHT_JOBS = 32


class JobError(BaseModel):
    code: str
    message: str


def _validated_evidence(result: Any) -> dict[str, Any] | None:
    """Return the package's result, or ``None`` when it is not usable evidence.

    A write operation that reports success must hand back something an operator
    can inspect — the allocated record, the compiler counts, the refresh stages.
    ``None``, a non-mapping, or an empty mapping are all indistinguishable from
    "nothing happened" once rendered, so they are treated as a failed job rather
    than a success with an empty hand.
    """
    if not isinstance(result, Mapping) or not result:
        return None
    return dict(result)


class Job(BaseModel):
    """One failure-memory write operation.

    ``status`` is the only thing a UI may use to decide whether the operation
    happened. ``queued`` and ``running`` are explicitly NOT success: a form that
    clears itself on dispatch would be claiming an outcome the package has not
    reported yet.
    """

    id: str
    project_id: str
    action: JobAction
    status: JobStatus
    detail: str = Field(default="", description="Sub-action, e.g. the refresh stage.")
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    summary: str = ""
    result: dict[str, Any] | None = Field(
        default=None,
        description="The package's own return value. Present only on success.",
    )
    error: JobError | None = Field(
        default=None, description="Present only on failure; the package's own code."
    )

    @property
    def terminal(self) -> bool:
        return self.status in ("succeeded", "failed")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JobAdmissionError(RuntimeError):
    """Raised when the in-flight queue is full. The submission is REJECTED.

    Deliberately an error rather than a silent drop or an unbounded accept: the
    caller must learn that its work was not taken, and the only alternative to
    rejecting is accepting work we cannot bound.
    """

    def __init__(self, message: str, *, inflight: int, limit: int) -> None:
        super().__init__(message)
        self.code = "job_queue_full"
        self.inflight = inflight
        self.limit = limit


class JobRegistry:
    """Job registry with an optional on-disk journal. Snapshots handed out by value.

    ``journal_path`` names a JSON document owned by Logic-Loom (under its own
    cache directory — never inside any failure-memory project). It is rewritten
    (best effort; atomic replace + fsync) on admission, terminal publication
    and reap, and reloaded on startup, so results survive an ordinary sidecar
    restart. A registry constructed without one (tests) keeps every property
    except restart durability.
    """

    def __init__(
        self,
        *,
        max_retained: int = MAX_RETAINED_JOBS,
        max_inflight: int = MAX_INFLIGHT_JOBS,
        journal_path: Path | None = None,
    ) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._guard = threading.RLock()
        # Created lazily on first submit, and shut down with the registry. A
        # ThreadPoolExecutor worker is NOT a daemon thread: it parks in
        # Condition.wait() and concurrent.futures joins it from an atexit hook.
        # Constructing one eagerly meant every registry -- including the many a
        # test suite builds and never submits to -- left an idle thread for the
        # interpreter to join at exit. On Windows that surfaced as pytest
        # reporting "818 passed" and then dying with KeyboardInterrupt in
        # threading.py Condition.wait() while shutting down, exit code 1.
        self._pool: ThreadPoolExecutor | None = None
        self._atexit_hook: Callable[[], None] | None = None
        self._max_retained = max_retained
        self._max_inflight = max_inflight
        self._journal_path: Path | None = None
        if journal_path is not None:
            self.attach_journal(journal_path)

    # ── journal (best effort) ───────────────────────────────────────────────

    def attach_journal(self, journal_path: Path) -> None:
        """Adopt *journal_path*, reloading any jobs a previous process left.

        Jobs already in memory take precedence over journalled ones with the
        same id. A journalled job that never reached a terminal state belonged
        to a process that died mid-write; it is republished as ``failed`` with
        ``interrupted_by_restart`` so its submitter learns the truth rather
        than meeting a 404 indistinguishable from "never ran".
        """
        with self._guard:
            if self._journal_path is not None:
                return
            self._journal_path = journal_path
            restored = self._read_journal(journal_path)
            if restored:
                merged: OrderedDict[str, Job] = OrderedDict()
                for job in restored:
                    if job.id not in self._jobs:
                        merged[job.id] = job
                merged.update(self._jobs)
                self._jobs = merged
            self._persist()

    @staticmethod
    def _read_journal(journal_path: Path) -> list[Job]:
        try:
            raw = json.loads(journal_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError):
            logger.exception("failure-memory job journal unreadable: %s", journal_path)
            return []
        jobs: list[Job] = []
        for entry in raw.get("jobs") or []:
            try:
                job = Job.model_validate(entry)
            except ValidationError:
                logger.warning("failure-memory job journal entry ignored: %r", entry)
                continue
            if not job.terminal:
                # The process that accepted this job died before the package
                # reported an outcome. The write may or may not have landed —
                # the package's own lock and atomic-replace semantics protect
                # the repository — but this surface can no longer learn the
                # result, and claiming anything else would be a guess.
                job = job.model_copy(
                    update={
                        "status": "failed",
                        "finished_at": _now(),
                        "error": JobError(
                            code="interrupted_by_restart",
                            message=(
                                "The sidecar restarted while this job was "
                                f"{job.status}. Its outcome is unknown to this surface: "
                                "check the repository (git status / the failure-memory "
                                "registry) and re-submit if the write did not land."
                            ),
                        ),
                        "summary": "Interrupted by a sidecar restart; outcome unknown.",
                    }
                )
            jobs.append(job)
        return jobs

    def _persist(self) -> None:
        """Write the journal atomically, best effort. Called under ``self._guard``.

        A failed write never blocks the in-memory truth; it costs restart
        durability only, and is logged rather than swallowed silently.
        """
        if self._journal_path is None:
            return
        try:
            payload = json.dumps(
                {"version": 1, "jobs": [job.model_dump() for job in self._jobs.values()]},
                ensure_ascii=False,
            )
            self._journal_path.parent.mkdir(parents=True, exist_ok=True)
            scratch = self._journal_path.with_name(self._journal_path.name + ".tmp")
            with open(scratch, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(scratch, self._journal_path)
        except OSError:
            logger.exception(
                "failure-memory job journal write failed: %s — job history will not "
                "survive a restart",
                self._journal_path,
            )

    # ── admission ───────────────────────────────────────────────────────────

    def inflight(self) -> int:
        with self._guard:
            return sum(1 for job in self._jobs.values() if not job.terminal)

    def submit(
        self,
        *,
        project_id: str,
        action: JobAction,
        detail: str,
        work: Callable[[], dict[str, Any]],
        summarize: Callable[[dict[str, Any]], str],
    ) -> Job:
        """Queue *work* and return the job as it exists BEFORE anything ran.

        Raises :class:`JobAdmissionError` when the in-flight queue is full. The
        capacity check and the insertion share ONE lock acquisition — split
        across two, concurrent submitters all pass the check and exceed the
        limit. A rejected submission creates nothing and schedules nothing.
        """
        job = Job(
            id=uuid.uuid4().hex,
            project_id=project_id,
            action=action,
            detail=detail,
            status="queued",
            created_at=_now(),
        )
        with self._guard:
            inflight = sum(1 for existing in self._jobs.values() if not existing.terminal)
            if inflight >= self._max_inflight:
                raise JobAdmissionError(
                    f"{inflight} failure-memory job(s) are already queued or running "
                    f"(limit {self._max_inflight}). This submission was rejected rather "
                    "than queued; nothing was started. Wait for the queue to drain.",
                    inflight=inflight,
                    limit=self._max_inflight,
                )
            self._jobs[job.id] = job
            self._reap()
            self._persist()
        self._executor().submit(self._run, job.id, work, summarize)
        return job

    def _executor(self) -> ThreadPoolExecutor:
        """The worker pool, created on first use."""
        with self._guard:
            if self._pool is None:
                pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fm-job")
                # Callers are not required to remember shutdown(), so make
                # interpreter exit safe unconditionally. atexit runs LIFO and
                # concurrent.futures registers its joining _python_exit at
                # import time, so this hook fires FIRST: it drains the queue and
                # wakes the worker, and the subsequent join finds a thread that
                # is already on its way out instead of one parked in
                # Condition.wait().
                self._atexit_hook = functools.partial(
                    pool.shutdown, wait=False, cancel_futures=True
                )
                atexit.register(self._atexit_hook)
                self._pool = pool
            return self._pool

    def shutdown(self, *, wait: bool = True) -> None:
        """Release the worker thread. Idempotent; safe if nothing was submitted."""
        with self._guard:
            pool, self._pool = self._pool, None
            hook, self._atexit_hook = self._atexit_hook, None
        if hook is not None:
            atexit.unregister(hook)
        if pool is not None:
            pool.shutdown(wait=wait)

    # ── retention ───────────────────────────────────────────────────────────

    def _reap(self) -> None:
        """Trim retained history — TERMINAL jobs only, oldest first.

        Must be called under ``self._guard``, and NEVER from the code path
        that publishes a terminal state — a poller mid-publication must see
        the outcome, not ``running -> missing``. If every retained job is
        still in flight the cap is deliberately exceeded: a job that may
        still write is not a log entry to be trimmed. No time-based eviction.
        """
        if len(self._jobs) <= self._max_retained:
            return
        for job_id, job in list(self._jobs.items()):
            if len(self._jobs) <= self._max_retained:
                return
            if job.terminal:
                del self._jobs[job_id]

    # ── execution ───────────────────────────────────────────────────────────

    def _run(
        self,
        job_id: str,
        work: Callable[[], dict[str, Any]],
        summarize: Callable[[dict[str, Any]], str],
    ) -> None:
        self._patch(job_id, status="running", started_at=_now())
        try:
            raw = work()
        except Exception as exc:  # noqa: BLE001 — every failure is a job failure
            logger.info("failure-memory job %s failed", job_id, exc_info=True)
            self._patch(
                job_id,
                status="failed",
                finished_at=_now(),
                error=JobError(
                    code=str(getattr(exc, "code", None) or "failure_memory_error"),
                    message=str(exc),
                ),
                summary=str(exc),
            )
            return
        result = _validated_evidence(raw)
        if result is None:
            # The callable returned without raising but produced no inspectable
            # evidence. Publishing that as a success would be a green tick over
            # an empty hand — the exact shape of a falsely accepted write.
            self._patch(
                job_id,
                status="failed",
                finished_at=_now(),
                error=JobError(
                    code="missing_result_evidence",
                    message=(
                        "The operation returned without raising but produced no result to "
                        f"show for it (got {type(raw).__name__}). 'Did not raise' is not "
                        "'produced a result', so this is reported as failed rather than as "
                        "a success with nothing in it."
                    ),
                ),
                summary="Completed without evidence; reported as failed.",
            )
            return
        # Success and its evidence are published in ONE update. A job can never
        # be observed as succeeded-without-result, which is the shape a UI would
        # misread as "done" while holding nothing to show for it.
        try:
            summary = summarize(result)
        except Exception:  # pragma: no cover - summaries must never fail a job
            logger.exception("failure-memory job %s summary failed", job_id)
            summary = "Completed."
        self._patch(
            job_id,
            status="succeeded",
            finished_at=_now(),
            result=result,
            summary=summary,
        )

    def _patch(self, job_id: str, **fields: Any) -> None:
        with self._guard:
            job = self._jobs.get(job_id)
            if job is None:  # pragma: no cover - defensive; nonterminal never evicted
                return
            self._jobs[job_id] = job.model_copy(update=fields)
            # Deliberately NO reaping here: publication must never race a
            # poller out of its outcome. Reaping happens only at submission.
            if fields.get("status") in ("succeeded", "failed"):
                self._persist()

    # ── reads ───────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Job | None:
        with self._guard:
            job = self._jobs.get(job_id)
            return None if job is None else job.model_copy(deep=True)

    def list(self, *, project_id: str | None = None, limit: int = 50) -> list[Job]:
        """Newest-first listing, optionally scoped to one project."""
        with self._guard:
            jobs = [job.model_copy(deep=True) for job in self._jobs.values()]
        if project_id is not None:
            jobs = [job for job in jobs if job.project_id == project_id]
        return list(reversed(jobs))[:limit]

    def wait(self, job_id: str, timeout: float = 30.0) -> Job | None:
        """Block until the job reaches a terminal state. Tests and the CLI only."""
        deadline = datetime.now(UTC).timestamp() + timeout
        while datetime.now(UTC).timestamp() < deadline:
            job = self.get(job_id)
            if job is None or job.terminal:
                return job
            threading.Event().wait(0.02)
        return self.get(job_id)


_registry: JobRegistry | None = None
_registry_guard = threading.RLock()


def _journal_path(settings: "Settings") -> Path:
    """Logic-Loom-owned journal location — inside its cache, never a project."""
    return settings.cache_path / "failure-memory" / "jobs.json"


def get_job_registry(settings: "Settings | None" = None) -> JobRegistry:
    """The process-wide registry.

    Production callers (the route layer and the operator service) pass their
    ``Settings`` so the registry carries the journal; a bare call returns the
    same singleton.
    """
    global _registry
    with _registry_guard:
        if _registry is None:
            _registry = JobRegistry(
                journal_path=_journal_path(settings) if settings is not None else None
            )
        elif settings is not None:
            _registry.attach_journal(_journal_path(settings))
        return _registry


__all__ = [
    "Job",
    "JobAction",
    "JobAdmissionError",
    "JobError",
    "JobRegistry",
    "JobStatus",
    "get_job_registry",
]
