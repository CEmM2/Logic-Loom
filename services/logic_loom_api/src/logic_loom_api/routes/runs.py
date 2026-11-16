"""``/api/runs/*`` routes — run-workflow + run-history.

  * ``POST /api/runs/workflow`` — run a workflow against a batch.
  * ``GET /api/runs`` — list workflow runs (kind filter).
  * ``GET /api/runs/{run_id}`` — single run record.
  * ``GET /api/runs/{run_id}/effective-prompts`` — read snapshot from disk.
  * ``POST /api/runs/{run_id}/rerun`` — copy a source run's settings,
    apply optional new overrides, mint a fresh run (no source mutation).
  * ``POST /api/runs/{run_id}/cancel`` — registry-level cancel.

Static routes (``/workflow``) MUST be declared BEFORE any parametric
``/{run_id}`` route. FastAPI matches by registration order, so a
parametric route registered first would capture ``/workflow`` as
``run_id="workflow"`` and 404 the workflow handler (the same trap
hit the batches export-plan route). ``GET /api/runs/{run_id}/stream`` is
declared after all other routes — the order ``static → parametric`` is the
load-bearing invariant.

The route layer remains a thin adapter: service-level errors mapped to
HTTPException, registry-level errors mapped to 404/409 as appropriate.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sse_starlette.sse import EventSourceResponse

from ..config import Settings
from ..runs.output_store import OutputStore
from ..runs.registry import get_registry
from ..runs.service import (
    BatchNotFoundError,
    PromptResolutionError,
    RunService,
    RunServiceError,
    RunWorkflowRequest,
    WorkflowNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _service() -> RunService:
    """Construct a per-request :class:`RunService` against current settings."""
    return RunService(Settings.load())


# ---------------------------------------------------------------------------
# Static routes (declared first — see module docstring)
# ---------------------------------------------------------------------------


@router.post("/workflow")
async def run_workflow(body: RunWorkflowRequest) -> dict[str, Any]:
    """Run a workflow against a batch.

    ``dry_run=true`` returns a payload with the resolved variants
    and the would-be output_dir, without spawning a subprocess.

    For ``akms.node_extraction`` (non-dry), this delegates to the AKMS
    node-generation launch path so the executor is actually attached
    (register-only was leaving runs forever-``pending`` — nlm_batch_fix).
    Empty ``nlm_id`` / ``notebook_id`` means create a new NotebookLM notebook.
    """
    # AKMS node extraction needs the full dispatch + subprocess attach.
    # Keep dry_run on the generic path (preview only, no spawn / no nlm create).
    if body.workflow_id == "akms.node_extraction" and not body.dry_run:
        from .akms_dispatch import NodeGenerationRunRequest, create_run as create_node_gen_run

        wi = dict(body.workflow_inputs or {})
        ng_body = NodeGenerationRunRequest(
            batch_id=body.batch_id,
            notebook_id=body.notebook_id,
            nlm_id=wi.get("nlm_id") or body.notebook_id,
            node_selection=wi.get("node_selection"),
            plan_path=wi.get("plan_path"),
            prompt_profile_id=body.prompt_profile_id,
            prompt_overrides=dict(body.prompt_overrides or {}),
            run_label=body.run_label,
            out_dir=body.out_dir,
            force=body.force,
            dry_run=False,
            save_as_batch_default=body.save_as_batch_default,
        )
        return await create_node_gen_run(ng_body)

    try:
        return _service().start_workflow(body)
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except PromptResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RunServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc


@router.get("")
def list_runs(
    kind: str | None = Query(default=None, description="Filter by RunKind."),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """List runs, optionally filtered by ``kind`` (``zsum`` or ``workflow``).

    The Runs page uses this with ``kind=workflow`` to
    show workflow runs in a separate tab from the legacy zsum history.
    """
    runs = get_registry().list(limit=limit, kind=kind)
    return {"runs": [r.model_dump() for r in runs]}


# ---------------------------------------------------------------------------
# Bulk clear (static — declared before parametric routes)
# ---------------------------------------------------------------------------


class ClearRunsRequest(BaseModel):
    """Body for ``POST /api/runs/clear``.

    ``statuses`` is the set of terminal statuses to clear. Defaults to
    every terminal status. Non-terminal statuses are silently ignored.
    """

    model_config = ConfigDict(extra="ignore")

    statuses: list[str] = Field(
        default_factory=lambda: ["succeeded", "failed", "cancelled"]
    )


@router.post("/clear")
def clear_runs(body: ClearRunsRequest | None = None) -> dict[str, int]:
    """Bulk-delete terminal runs. Returns the number removed."""
    statuses = tuple((body or ClearRunsRequest()).statuses)
    deleted = get_registry().delete_terminal(statuses=statuses)
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Rerun body
# ---------------------------------------------------------------------------


class RerunRequest(BaseModel):
    """Optional payload for ``POST /api/runs/{run_id}/rerun``.

    All fields are optional — the rerun copies the source run's settings
    by default. Anything supplied here overrides the corresponding source
    field. ``prompt_overrides`` is MERGED into the source's overrides so
    the user can flip one slot without rebuilding the whole map.
    """

    model_config = ConfigDict(extra="ignore")

    run_label: str | None = None
    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] | None = None
    notebook_id: str | None = None
    out_dir: str | None = None
    force: bool = False
    dry_run: bool = False
    save_as_batch_default: bool = False
    workflow_inputs: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Parametric routes (declared AFTER the static routes above)
# ---------------------------------------------------------------------------


@router.get("/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """Get a single run record from the registry."""
    rec = get_registry().get(run_id)
    if rec is None:
        raise HTTPException(404, detail=f"run {run_id!r} not found")
    return rec.model_dump()


@router.get("/{run_id}/effective-prompts")
def get_effective_prompts(run_id: str) -> dict[str, Any]:
    """Read the effective-prompts manifest from the on-disk snapshot.

    Contract: this endpoint reads from the snapshot rather than
    recomputing. That makes run Compare deterministic even if the
    underlying prompt variants have since been edited.
    """
    rec = get_registry().get(run_id)
    if rec is None:
        raise HTTPException(404, detail=f"run {run_id!r} not found")
    # Locate the snapshot. Workflow runs carry batch_id + workflow_id; the
    # log_path lives at <run_dir>/logs.txt, so the run_dir is its parent.
    log_path = Path(rec.log_path) if rec.log_path else None
    if log_path is None or not log_path.exists():
        raise HTTPException(404, detail="run snapshot not available on disk")
    run_dir = log_path.parent
    try:
        return OutputStore.read_effective_prompts(run_dir)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, str]:
    """Cancel a running registry run (SIGTERM). Workflow runs without a
    live subprocess attached return 409."""
    reg = get_registry()
    rec = reg.get(run_id)
    if rec is None:
        raise HTTPException(404, detail=f"run {run_id!r} not found")
    ok = reg.cancel(run_id)
    if not ok:
        raise HTTPException(
            409, detail="run not running or has no live subprocess"
        )
    return {"status": "cancelling"}


@router.post("/{run_id}/rerun")
async def rerun(run_id: str, body: RerunRequest | None = None) -> dict[str, Any]:
    """Rerun a workflow run with optional overrides.

    Creates a fresh run directory and writes a new snapshot; the source
    run is unchanged after rerun.

    Mechanism: read the source run's ``metadata.json`` (the source of
    truth for what the source run was actually invoked with), copy the
    settings into a :class:`RunWorkflowRequest`, layer the rerun body on
    top, and delegate to :func:`run_workflow` (so ``akms.node_extraction``
    still gets a live executor attach — nlm_batch_fix). The service
    mints a fresh ``run_id`` and writes to a fresh directory; the source
    run's directory and metadata are never opened for writing.
    """
    reg = get_registry()
    src = reg.get(run_id)
    if src is None:
        raise HTTPException(404, detail=f"run {run_id!r} not found")
    if src.kind != "workflow" or not src.batch_id or not src.workflow_id:
        raise HTTPException(
            400, detail="rerun is only supported for workflow runs"
        )
    log_path = Path(src.log_path) if src.log_path else None
    if log_path is None or not log_path.exists():
        raise HTTPException(404, detail="source run snapshot not on disk")
    src_dir = log_path.parent
    try:
        src_meta = OutputStore.read_metadata(src_dir)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc

    # Build the new RunWorkflowRequest from source metadata + rerun overrides.
    b = body or RerunRequest()
    base_overrides = dict(src_meta.get("prompt_overrides") or {})
    if b.prompt_overrides is not None:
        # Merge — the source's overrides set the floor; the rerun body
        # specifies which slots to flip. Setting save_as_batch_default
        # would then write the merged map back into the batch JSON.
        base_overrides.update(b.prompt_overrides)

    new_req = RunWorkflowRequest(
        batch_id=src.batch_id,
        workflow_id=src.workflow_id,
        notebook_id=(
            b.notebook_id if b.notebook_id is not None else src_meta.get("notebook_id")
        ),
        prompt_profile_id=(
            b.prompt_profile_id
            if b.prompt_profile_id is not None
            else src_meta.get("prompt_profile_id")
        ),
        prompt_overrides=base_overrides,
        run_label=b.run_label if b.run_label is not None else src_meta.get("run_label"),
        save_as_batch_default=b.save_as_batch_default,
        out_dir=b.out_dir,
        force=b.force,
        dry_run=b.dry_run,
        workflow_inputs=(
            dict(b.workflow_inputs)
            if b.workflow_inputs is not None
            else dict(src_meta.get("workflow_inputs") or {})
        ),
    )
    # Delegate through run_workflow so node-extraction re-runs attach the
    # executor (same as a fresh POST /api/runs/workflow).
    return await run_workflow(new_req)


@router.delete("/{run_id}")
def delete_run(run_id: str) -> dict[str, str]:
    """Delete a terminal run record.

    Returns 404 if the run is unknown, 409 if the run is still pending
    or running (caller must cancel first).
    """
    reg = get_registry()
    if reg.get(run_id) is None:
        raise HTTPException(404, detail=f"run {run_id!r} not found")
    try:
        deleted = reg.delete(run_id)
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(404, detail=f"run {run_id!r} not found")
    return {"status": "deleted", "run_id": run_id}


@router.get("/{run_id}/stream")
async def stream_run(run_id: str) -> EventSourceResponse:
    """Server-sent event stream of the run's appended log lines.

    Mirrors the existing zsum SSE handler shape but, for workflow runs,
    tails ``<run_dir>/logs.txt`` via :meth:`RunRegistry.stream_workflow_run`.
    Close-on-EOF when the run reaches a terminal state. The route uses
    ``sse_starlette.EventSourceResponse`` so the underlying ASGI server sets
    ``Content-Type: text/event-stream`` and disables intermediate buffering;
    the ``CorrelationIdMiddleware`` mounted in ``main.py`` still wraps the
    response so X-Correlation-ID flows through every streamed event.

    NB: this is the LAST parametric route declared on the runs router so
    static routes (POST /workflow, GET '') remain reachable. Adding any
    new parametric route AFTER this one is fine; adding any new static
    route requires inserting it ABOVE the parametric block — same
    first-match routing trap described in the module docstring.
    """
    reg = get_registry()
    rec = reg.get(run_id)
    if rec is None:
        raise HTTPException(404, detail=f"run {run_id!r} not found")

    async def gen():
        if rec.kind == "workflow":
            async for evt in reg.stream_workflow_run(run_id):
                yield {"event": "log", "data": json.dumps(evt.model_dump())}
        else:
            async for evt in reg.stream(run_id):
                yield {"event": "log", "data": json.dumps(evt.model_dump())}
        final = reg.get(run_id)
        yield {
            "event": "done",
            "data": json.dumps(final.model_dump() if final else {}),
        }

    return EventSourceResponse(gen())


__all__ = ["RerunRequest", "router"]
