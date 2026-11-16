"""``/api/failure-memory/*`` routes — the Failure Memory workspace.

Read-only surface::

    GET  /api/failure-memory/projects
    GET  /api/failure-memory/{project}/status
    GET  /api/failure-memory/{project}/lessons
    GET  /api/failure-memory/{project}/lessons/{lesson_id}
    GET  /api/failure-memory/{project}/routes
    POST /api/failure-memory/{project}/resolve
    POST /api/failure-memory/{project}/check

plus write diagnostics, background-job reads, and the three operator
endpoints (``record`` / ``refresh`` / ``compile``), which run only in
operator mode — outside it they return the typed disabled response
before anything else is considered.

Degrade-gracefully behaviour, following the ``/api/learn`` precedent already in
this service:

* ``GET /projects`` is ALWAYS 200. It is the discovery endpoint the workspace
  gate reads, and a read-only discovery call must never 503.
* Every per-project endpoint returns 503 when the optional package is absent,
  with the frozen consumer-contract degradation envelope as the response body
  (not this service's ``{"error": …}`` shape — those bytes are contract; see
  ``adapters.failure_memory.DEGRADATION_ENVELOPE``).
* Every per-project endpoint returns 503 ``failure_memory_disabled`` when
  ``failure_memory_mode = "disabled"``, and 404 ``unknown_project`` for an id
  that is not in ``[[failure_memory_projects]]``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ..adapters import failure_memory as adapter
from ..adapters.failure_memory import FailureMemoryAdapterError, ResolvedProject
from ..config import Settings
from ..deps import get_settings
from ..errors import envelope
from ..models.failure_memory import (
    CheckResponse,
    CompileRequest,
    LessonDetail,
    LessonsResponse,
    OperatorDisabledResponse,
    ProjectsResponse,
    RecordRequest,
    RefreshRequest,
    ResolveRequest,
    ResolveResponse,
    RoutesResponse,
    StatusResponse,
    WriteDiagnostics,
)
from ..services import failure_memory as service
from ..services import failure_memory_operator as operator_service
from ..services.failure_memory_jobs import Job, JobAdmissionError, get_job_registry

router = APIRouter()
logger = logging.getLogger(__name__)

#: Adapter error codes that mean "you asked for something that isn't there"
#: rather than "something went wrong reading it".
_NOT_FOUND_CODES = frozenset({"unknown_project", "unknown_lesson", "unknown_node"})


def _degradation_503() -> JSONResponse:
    """503 whose body is the frozen degradation envelope, byte-for-byte."""
    return JSONResponse(adapter.degradation_envelope(), status_code=503)


def _disabled_503(settings: Settings) -> JSONResponse:
    return JSONResponse(
        envelope(
            "failure_memory_disabled",
            "The Failure Memory workspace is disabled in Logic-Loom's configuration.",
            {"mode": settings.failure_memory_mode},
        ),
        status_code=503,
    )


def _queue_full_response(exc: JobAdmissionError) -> JSONResponse:
    """429 for a REJECTED submission. Nothing was queued and nothing started.

    An explicit rejection is the only honest alternative to accepting work we
    cannot bound: retaining every nonterminal job is correct, which is exactly
    why the queue itself has to be finite. Accepted work is never silently
    dropped, so a caller that gets a 202 can rely on it.
    """
    return JSONResponse(
        envelope(
            exc.code,
            str(exc),
            {"inflight": exc.inflight, "limit": exc.limit, "accepted": False},
        ),
        status_code=429,
    )


def _adapter_error_response(exc: FailureMemoryAdapterError) -> JSONResponse:
    status = 404 if exc.code in _NOT_FOUND_CODES else 412
    return JSONResponse(
        envelope(exc.code, str(exc), {"code": exc.code}), status_code=status
    )


def _guard(settings: Settings) -> JSONResponse | None:
    """Feature-level preconditions shared by every per-project endpoint."""
    if not settings.failure_memory_enabled():
        return _disabled_503(settings)
    if not adapter.failure_memory_available():
        return _degradation_503()
    return None


def _project(settings: Settings, project_id: str) -> ResolvedProject:
    return adapter.load_project(settings, project_id)


def _operator_guard(settings: Settings) -> JSONResponse | None:
    """Refuse every operator endpoint unless operator mode is explicitly on.

    Returns the typed disabled response, or ``None`` when the caller may
    proceed. This is the ONLY gate in front of
    ``adapters/failure_memory_operator.py``; the read-only adapter has no write
    capability to gate, which is why the gate can be this small and still be
    complete.

    Note the ordering in every operator handler: this check runs FIRST, before
    the project is resolved and before the write-capable module is used, so a
    read-only Logic-Loom never reaches operator code at all.
    """
    if settings.failure_memory_operator():
        return None
    payload = OperatorDisabledResponse(
        status="disabled",
        code="operator_mode_required",
        message=(
            "Operator endpoints require failure_memory_mode = 'operator'. Logic-Loom "
            "is running read-only and cannot modify any failure-memory project. The mode "
            "is deliberately not settable over HTTP — edit config.toml or set "
            "LOGIC_LOOM_FAILURE_MEMORY_MODE."
        ),
        mode=settings.failure_memory_mode,
        operator=False,
    )
    return JSONResponse(payload.model_dump(), status_code=403)


# ── discovery ───────────────────────────────────────────────────────────────


@router.get("/projects", response_model=ProjectsResponse)
def failure_memory_projects(settings: Settings = Depends(get_settings)) -> ProjectsResponse:
    """List the EXPLICITLY configured projects. Always 200, never scans disk."""
    return service.build_projects(settings)


# ── per-project reads ───────────────────────────────────────────────────────


@router.get("/{project}/status", response_model=StatusResponse)
def failure_memory_status(project: str, settings: Settings = Depends(get_settings)):
    blocked = _guard(settings)
    if blocked is not None:
        return blocked
    try:
        return service.build_status(settings, _project(settings, project))
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)


@router.get("/{project}/lessons", response_model=LessonsResponse)
def failure_memory_lessons(
    project: str,
    path: str | None = Query(default=None, description="Substring match on declared or routed paths."),
    symbol: str | None = Query(default=None, description="Substring match on location.symbol or routed symbols."),
    category: str | None = Query(default=None, description="Substring match on found_by.type or location.area."),
    root_cause: str | None = Query(default=None, description="Substring match on root_cause."),
    related: str | None = Query(default=None, description="Exact lesson id; matches co-activated lessons in both directions."),
    q: str | None = Query(default=None, description="Free-text match across the lesson body."),
    settings: Settings = Depends(get_settings),
):
    blocked = _guard(settings)
    if blocked is not None:
        return blocked
    try:
        return service.build_lessons(
            _project(settings, project),
            path=path,
            symbol=symbol,
            category=category,
            root_cause=root_cause,
            related=related,
            query=q,
        )
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)


@router.get("/{project}/lessons/{lesson_id}", response_model=LessonDetail)
def failure_memory_lesson(
    project: str, lesson_id: str, settings: Settings = Depends(get_settings)
):
    blocked = _guard(settings)
    if blocked is not None:
        return blocked
    try:
        return service.build_lesson_detail(_project(settings, project), lesson_id)
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)


@router.get("/{project}/routes", response_model=RoutesResponse)
def failure_memory_routes(project: str, settings: Settings = Depends(get_settings)):
    """Compiled route coverage plus the graph overlay for this project."""
    blocked = _guard(settings)
    if blocked is not None:
        return blocked
    try:
        return service.build_routes(_project(settings, project))
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)


@router.post("/{project}/resolve", response_model=ResolveResponse)
def failure_memory_resolve(
    project: str,
    payload: ResolveRequest,
    settings: Settings = Depends(get_settings),
):
    """Preview the provider result for a task or diff.

    POST because the request body is a task description, not because anything is
    created: the underlying call runs with ``write_artifacts=False`` and leaves
    no invocation directory behind. ``result`` is the provider's own payload,
    passed through unchanged so it can be diffed against the frozen fixture.
    """
    blocked = _guard(settings)
    if blocked is not None:
        return blocked
    try:
        return service.build_resolve(_project(settings, project), payload)
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)


@router.post("/{project}/check", response_model=CheckResponse)
def failure_memory_check(project: str, settings: Settings = Depends(get_settings)):
    """Compiler diagnostics: ``validate`` + ``check``. Publishes nothing."""
    blocked = _guard(settings)
    if blocked is not None:
        return blocked
    try:
        return service.build_check(settings, _project(settings, project))
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)


# ── write diagnostics (readable in any mode) ────────────────────────────────


@router.get("/{project}/write-diagnostics", response_model=WriteDiagnostics)
def failure_memory_write_diagnostics(
    project: str, settings: Settings = Depends(get_settings)
):
    """Can this Logic-Loom write to this project, and if not, exactly what blocks it?

    Readable in READ-ONLY mode on purpose: "would refresh work if I turned
    operator mode on?" is a question worth answering before turning it on, and
    answering it requires no write capability — the underlying
    ``refresh.preflight`` is public and explicitly non-mutating.
    """
    blocked = _guard(settings)
    if blocked is not None:
        return blocked
    try:
        return operator_service.build_write_diagnostics(settings, _project(settings, project))
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)


# ── background jobs ─────────────────────────────────────────────────────────


@router.get("/jobs", response_model=list[Job])
def failure_memory_jobs(
    project: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    settings: Settings = Depends(get_settings),
):
    """Write-job history. Readable in any mode so a read-only viewer can see what
    an operator instance did."""
    blocked = _guard(settings)
    if blocked is not None:
        return blocked
    return get_job_registry(settings).list(project_id=project, limit=limit)


@router.get("/jobs/{job_id}", response_model=Job)
def failure_memory_job(job_id: str, settings: Settings = Depends(get_settings)):
    blocked = _guard(settings)
    if blocked is not None:
        return blocked
    job = get_job_registry(settings).get(job_id)
    if job is None:
        return JSONResponse(
            envelope("unknown_job", f"No failure-memory job {job_id!r}.", None),
            status_code=404,
        )
    return job


# ── operator endpoints ──────────────────────────────────────────────────────
#
# All three return 202 with a Job whose status is `queued` — deliberately NOT a
# success. Nothing here reports an outcome; the outcome arrives only when the
# package returns and the job registry publishes `succeeded` together with the
# package's own result. A UI that treated the 202 as "done" would be claiming an
# outcome that has not happened yet, which is precisely the partially-accepted
# state this design exists to prevent.


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _validate(model: type[_ModelT], raw: Any) -> _ModelT | JSONResponse:
    """Validate an operator request body AFTER the permission gate has passed.

    The three operator endpoints take their bodies as raw objects rather than as
    typed parameters, purely so that ordering holds. FastAPI validates a typed
    parameter BEFORE the handler runs, so a read-only Logic-Loom answered a
    malformed ``POST /record`` with 422 instead of the typed disabled
    response — the permission answer depended on the shape of the body, which is
    exactly backwards. Nothing could mutate either way, but "is this instance
    allowed to write?" must be answered before anything else is considered.
    """
    try:
        return model.model_validate(raw if raw is not None else {})
    except ValidationError as exc:
        return JSONResponse(
            envelope(
                "validation_error",
                "One or more request fields failed validation.",
                {"errors": json.loads(exc.json())},
            ),
            status_code=422,
        )


@router.post("/{project}/record", response_model=Job, status_code=202)
def failure_memory_record(
    project: str,
    payload: dict[str, Any] | None = Body(default=None),
    settings: Settings = Depends(get_settings),
):
    """Queue one recording through the package's public recorder.

    Logic-Loom never appends to ``lessons.json``. It forwards this validated
    proposal to ``record.add_lesson``, which allocates the id under the project
    lock, validates the CANDIDATE registry before publishing it, replaces the
    file atomically, compiles the generated tree in write mode, and restores the
    previous registry bytes if that compile raises. Every one of those is a
    guarantee an "open JSON, append dict, save" shortcut would silently discard.
    """
    blocked = _operator_guard(settings) or _guard(settings)
    if blocked is not None:
        return blocked
    request = _validate(RecordRequest, payload)
    if isinstance(request, JSONResponse):
        return request
    try:
        resolved = _project(settings, project)
        return operator_service.submit_record(settings, resolved, request)
    except JobAdmissionError as exc:
        return _queue_full_response(exc)
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)


@router.post("/{project}/compile", response_model=Job, status_code=202)
def failure_memory_compile(
    project: str,
    payload: dict[str, Any] | None = Body(default=None),
    settings: Settings = Depends(get_settings),
):
    """Queue ``run_compiler(mode="write")``.

    Expect a whole-tree rewrite: every generated node embeds the whole-registry
    digest, so any registry change rewrites every node file. The job summary
    says so rather than leaving a full-tree diff looking like damage.
    """
    blocked = _operator_guard(settings) or _guard(settings)
    if blocked is not None:
        return blocked
    request = _validate(CompileRequest, payload)
    if isinstance(request, JSONResponse):
        return request
    try:
        return operator_service.submit_compile(settings, _project(settings, project))
    except JobAdmissionError as exc:
        return _queue_full_response(exc)
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)


@router.post("/{project}/refresh", response_model=Job, status_code=202)
def failure_memory_refresh(
    project: str,
    payload: dict[str, Any] | None = Body(default=None),
    settings: Settings = Depends(get_settings),
):
    """Queue one refresh stage through ``refresh.refresh_project``.

    ``clean`` is deliberately not an accepted action — it deletes every artifact
    the project marks disposable, and that does not belong one click from a
    lesson inventory. ``generated_at`` defaults to a value derived from registry
    content, never the wall clock.

    On an ordinary install this will fail with a ``repo2md_*`` code: every
    mutating refresh runs a toolchain preflight requiring a pinned, clean,
    EDITABLE repo2md checkout. That is a toolchain shortfall — ``unavailable``,
    meaning FIX THE TOOLCHAIN — and never staleness, which would mean the
    knowledge moved. ``GET /write-diagnostics`` reports the distinction before
    the operator tries.
    """
    blocked = _operator_guard(settings) or _guard(settings)
    if blocked is not None:
        return blocked
    request = _validate(RefreshRequest, payload)
    if isinstance(request, JSONResponse):
        return request
    try:
        return operator_service.submit_refresh(
            settings, _project(settings, project), request
        )
    except JobAdmissionError as exc:
        return _queue_full_response(exc)
    except FailureMemoryAdapterError as exc:
        return _adapter_error_response(exc)
