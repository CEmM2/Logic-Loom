"""``/api/akms/node-generation/*`` routes.

Preflight:

  * ``POST /api/akms/node-generation/preflight`` — runs the 12-check
    preflight and returns the structured
    ``{ok, checks[]}`` payload. Missing tools (``claude``, ``nlm``)
    show up as failed checks with detail ``"not found on PATH"`` —
    they NEVER raise.

The router also carries run-management endpoints
(``POST /runs``, ``GET /runs/{id}``, ``GET /runs/{id}/stream``,
``POST /runs/{id}/cancel``) — see the second half of the file.

The router is mounted in :mod:`logic_loom_api.main` under the prefix
``/api/akms/node-generation``. Behind the
degrade-gracefully try/except wrapper a router-construction failure
logs and the rest of the sidecar comes up; individual endpoints return
503 when the AKMS extra is missing rather than crashing the app.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sse_starlette.sse import EventSourceResponse

from ..akms_claude_runtime import (
    codex_runtime_dir,
    materialize_codex_runtime,
    materialize_runtime_workspace,
    runtime_dir,
)
from ..akms_dispatch import (
    AkmsDispatchError,
    build_node_gen_dispatch,
    preflight_node_generation,
    require_node_gen_binary,
    resolve_nlm_id_for_batch,
)
from ..batches import BatchStore
from ..config import Settings
from ..runs.output_store import LOG_FILENAME
from ..runs.registry import get_registry
from ..runs.service import (
    BatchNotFoundError,
    PromptResolutionError,
    RunService,
    RunServiceError,
    RunWorkflowRequest,
    WorkflowNotFoundError,
)

#: Workflow id used for AKMS node-generation runs. The
#: corresponding bundled workflow lives at
#: ``resources/workflow_library/defaults/akms.node_extraction.json``.
AKMS_NODE_EXTRACTION_WORKFLOW = "akms.node_extraction"

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


class PreflightRequest(BaseModel):
    """Payload for ``POST /api/akms/node-generation/preflight``.

    All fields are optional — the route uses the active settings to fill
    in defaults so the frontend can submit an empty body during the
    initial "is AKMS workable?" smoke check, then provide ``nlm_id`` /
    ``plan_path`` / ``out_dir`` once the user has selected them in the
    Run dialog.
    """

    model_config = ConfigDict(extra="ignore")

    nlm_id: str | None = None
    plan_path: str | None = None
    out_dir: str | None = None
    akms_vault: str | None = None
    claude_binary: str = "claude"
    nlm_binary: str = "nlm"


@router.post("/preflight")
async def preflight(body: PreflightRequest | None = None) -> dict[str, Any]:
    """Run the 12-check preflight. Returns ``{"ok": bool, "checks": [...]}``.

    A missing ``claude`` yields
    ``{name: "claude_cli", ok: false, detail: "not found on PATH"}``
    without raising. Same for ``nlm``.
    """
    body = body or PreflightRequest()
    settings = Settings.load()

    # Resolve defaults from settings when the body omits them.
    akms_vault: Path | str | None = body.akms_vault
    if akms_vault is None:
        try:
            akms_vault = settings.effective_akms_global_vault()
        except Exception:  # noqa: BLE001 — preflight must never raise
            akms_vault = None

    return await preflight_node_generation(
        nlm_id=body.nlm_id,
        plan_path=Path(body.plan_path) if body.plan_path else None,
        out_dir=Path(body.out_dir) if body.out_dir else None,
        akms_vault=akms_vault,
        claude_binary=body.claude_binary,
        nlm_binary=body.nlm_binary,
        # Pass settings so the preflight checks the host-chosen
        # agent binary (e.g. codex for codex-cli) instead of hardcoded claude.
        # resolve_node_gen_runtime degrades to the claude fallback on any error.
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Node-generation run routes
#
# AKMS dispatch is a SPECIALISATION of the generic workflow run system,
# not a parallel path. Every endpoint here delegates into the same
# RunService + RunRegistry the generic ``/api/runs/*`` routes
# use, so a run minted at ``POST /api/akms/node-generation/runs`` is
# visible at ``GET /api/runs/<id>`` AND ``GET /api/akms/node-generation/runs/<id>``
# without duplication.
# ---------------------------------------------------------------------------


class NodeGenerationRunRequest(BaseModel):
    """Payload for ``POST /api/akms/node-generation/runs``.

    The shape is a thin AKMS-flavoured wrapper around
    :class:`RunWorkflowRequest`: ``workflow_id`` is fixed to
    ``akms.node_extraction`` and the AKMS-specific knobs (``nlm_id``,
    ``node_selection``, ``plan_path``) are surfaced as first-class
    fields. They map onto ``workflow_inputs`` so the bundled workflow
    sees them in the canonical shape.
    """

    model_config = ConfigDict(extra="ignore")

    batch_id: str
    notebook_id: str | None = None
    nlm_id: str | None = None
    node_selection: str | None = None
    plan_path: str | None = None
    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    run_label: str | None = None
    out_dir: str | None = None
    force: bool = False
    dry_run: bool = False
    save_as_batch_default: bool = False


def _to_workflow_request(body: NodeGenerationRunRequest) -> RunWorkflowRequest:
    """Translate an AKMS run request into a generic workflow request.

    The AKMS-specific fields ride along under ``workflow_inputs`` so the
    bundled `akms.node_extraction` workflow can pick them up without
    growing a parallel schema.
    """
    workflow_inputs: dict[str, str] = {}
    if body.nlm_id:
        workflow_inputs["nlm_id"] = body.nlm_id
    if body.node_selection:
        workflow_inputs["node_selection"] = body.node_selection
    if body.plan_path:
        workflow_inputs["plan_path"] = body.plan_path
    return RunWorkflowRequest(
        batch_id=body.batch_id,
        workflow_id=AKMS_NODE_EXTRACTION_WORKFLOW,
        notebook_id=body.notebook_id,
        prompt_profile_id=body.prompt_profile_id,
        prompt_overrides=body.prompt_overrides,
        run_label=body.run_label,
        save_as_batch_default=body.save_as_batch_default,
        out_dir=body.out_dir,
        force=body.force,
        dry_run=body.dry_run,
        workflow_inputs=workflow_inputs,
    )


def _prepare_node_gen_run(body: NodeGenerationRunRequest) -> dict[str, Any]:
    """Load settings, gate the binary, mint the run, and build the dispatch.

    Runs ENTIRELY on a worker thread (via ``run_in_threadpool`` in
    :func:`create_run`) because every step is blocking I/O: ``Settings.load``
    reads the user TOML, :meth:`RunService.start_workflow` loads the batch +
    workflow, materialises the per-run ``.claude/`` runtime tree (a directory
    copy on first run / ``force``), and stakes out the run dir. Keeping it off
    the event loop means a first-run materialisation never stalls the sidecar.

    Returns ``{"payload": <RunService payload>, "attach": <attach kwargs | None>}``.
    ``attach`` is ``None`` for dry runs (status ``"preview"`` — no subprocess) and
    for any non-enqueued outcome; otherwise it carries the resolved provider argv /
    env / stdin (issue #103) for :meth:`RunRegistry.attach_workflow_subprocess`.

    Raises the same service-level errors as before (mapped to HTTP by
    :func:`create_run`). The dispatch BUILD never raises
    (:func:`build_node_gen_dispatch` degrades to the claude shape on any error).
    """
    # Settings.load() MUST stay inside the try so a malformed persisted config
    # maps to a 4xx, never a 500. A non-dict
    # `learn_llm_custom_clis` raises pydantic ValidationError (→ the route's
    # ValidationError handler); a malformed/unreadable TOML raises
    # TOMLDecodeError/OSError (→ 400 here).
    try:
        settings = Settings.load()
    except ValidationError:
        raise  # → mapped to 400 by the ValidationError handler in create_run
    except Exception as exc:  # noqa: BLE001 — bad config file is a client error, never a 500
        raise HTTPException(
            status_code=400,
            detail=f"invalid Logic-Loom configuration: {exc}",
        ) from exc

    # Fail-fast gate: chosen-but-missing CLI binary → AkmsDispatchError → 400.
    require_node_gen_binary(settings)

    # Resolve NotebookLM id BEFORE minting the run so dry-run previews and the
    # live prompt both see the real id. Empty request id is intentional: reuse
    # the batch's notebook when set, otherwise create a new one via `nlm
    # notebook create` and persist it on the batch (nlm_batch_fix).
    #
    # Fail on missing batch first so "unknown batch" stays 404 and never
    # attempts `nlm notebook create` (CI has no nlm; also wrong to create a
    # notebook for a batch that does not exist).
    workspace = settings.akms_workspace.expanduser()
    batch = BatchStore.load(workspace).get(body.batch_id)
    if batch is None:
        raise BatchNotFoundError(f"batch not found: {body.batch_id!r}")
    batch_notebook = batch.notebook_id or body.notebook_id
    # Skip create on dry_run — previews must not mutate NotebookLM / batches.
    if body.dry_run:
        resolved_nlm_id = (
            (body.nlm_id or "").strip()
            or (batch_notebook or "").strip()
            or ""
        )
        nlm_meta: dict[str, Any] = {
            "source": "explicit"
            if (body.nlm_id or "").strip()
            else ("batch" if (batch_notebook or "").strip() else "deferred"),
            "created": False,
        }
    else:
        resolved_nlm_id, nlm_meta = resolve_nlm_id_for_batch(
            explicit_nlm_id=body.nlm_id,
            batch_notebook_id=batch_notebook,
            batch_id=body.batch_id,
            batch_title=batch.title,
            workspace=workspace,
            persist=True,
        )

    # Thread the resolved id into workflow_inputs so metadata.json + the
    # generic RunService payload carry it for the Runs page.
    enriched = body.model_copy(deep=True)
    if resolved_nlm_id:
        # Prefer the resolved value for both fields so downstream consumers
        # (prompt snapshots, re-runs) see a concrete notebook id.
        enriched.nlm_id = resolved_nlm_id
        enriched.notebook_id = resolved_nlm_id

    payload = RunService(settings).start_workflow(_to_workflow_request(enriched))
    payload["nlm_id"] = resolved_nlm_id or None
    payload["nlm_notebook"] = nlm_meta

    # Only an enqueued (non-dry, registered) node-gen run gets a live subprocess.
    # Dry runs return status "preview" and are never registered (no run to attach).
    if payload.get("status") != "enqueued":
        return {"payload": payload, "attach": None}

    run_dir = Path(payload["out_dir"])
    # Build the provider-specific dispatch (full dispatch — issue #103): a CLI
    # choice (codex-cli / custom) → its own template argv; an akms API choice →
    # the claude shape + AKMS_LLM_* env; an nlm choice → the `nlm run` shape; no
    # choice → the claude fallback. Never raises.
    dispatch = build_node_gen_dispatch(
        settings=settings,
        nlm_id=resolved_nlm_id or "",
        node_selection=body.node_selection or "all",
        plan_path=Path(body.plan_path) if body.plan_path else run_dir / "plan.json",
        out_dir=run_dir,
        workflow_id=AKMS_NODE_EXTRACTION_WORKFLOW,
    )
    # The agent runs the bundled node-gen skill, so the cwd is a materialised skill
    # workspace, NOT the output dir (OUT_DIR rides in the prompt / argv). A host CLI
    # agent (codex / custom) discovers skills under .agents/skills/, so it gets a codex
    # runtime (issue #103); claude / akms / nlm get the claude runtime tree.
    #
    # Each run materialises a PER-RUN cwd (<base>/<run_id>) so two concurrently-
    # dispatched runs never share a workspace — a shared cwd let one run's force=True
    # rmtree wipe the skill tree another run's agent was executing from (issue #103).
    # The per-run dir is disposable; create_run reclaims it via an
    # on_done hook once the agent exits. If materialisation degrades, fall back to the
    # shared base (no cleanup) and let the spawn fail to a terminal status — never
    # a 500 here.
    run_id = payload["run_id"]
    base = codex_runtime_dir() if dispatch.is_cli_agent else runtime_dir()
    cleanup_cwd = False
    try:
        if dispatch.is_cli_agent:
            cwd = str(materialize_codex_runtime(force=True, target=base / run_id))
        else:
            cwd = str(materialize_runtime_workspace(force=True, target=base / run_id))
        cleanup_cwd = True
    except Exception as exc:  # noqa: BLE001 — degrade to the shared runtime cwd
        logger.warning(
            "per-run runtime materialisation failed for run %s (%s); using shared cwd",
            run_id,
            exc,
        )
        cwd = str(base)

    attach = {
        "run_id": run_id,
        "args": dispatch.argv,
        "cwd": cwd,
        "cleanup_cwd": cleanup_cwd,
        "log_path": str(run_dir / LOG_FILENAME),
        # env_overrides layer onto os.environ INSIDE the registry driver — never
        # written process-globally, where one run's provider env would leak into
        # the next run in the long-lived sidecar.
        "env": dispatch.env_overrides,
        "stdin": dispatch.stdin,
    }
    return {"payload": payload, "attach": attach}


@router.post("/runs")
async def create_run(body: NodeGenerationRunRequest) -> dict[str, Any]:
    """Create an AKMS node-generation run AND launch its executor (issue #103).

    Delegates to :class:`RunService` so the registered run is the same record
    visible at ``/api/runs/<id>``. The workflow is fixed to ``akms.node_extraction``.

    Before minting a run, the host-chosen node-gen agent binary is
    validated on PATH (:func:`require_node_gen_binary`). A chosen-but-missing CLI
    (e.g. ``codex-cli`` with ``codex`` not installed) fails fast with an actionable
    HTTP 400 naming the binary + provider. Malformed / unmapped / no-choice configs
    degrade to the claude fallback inside the gate and never reach this 400.

    Issue #103: for an enqueued (non-dry) run, the resolved provider runtime is
    threaded into the LIVE executor — :meth:`RunRegistry.attach_workflow_subprocess`
    spawns the provider-specific argv with the subprocess-scoped ``AKMS_LLM_*`` env
    (full dispatch: codex-cli / custom CLI / akms API / nlm / claude fallback). The
    handler is ``async`` so the registry's ``asyncio.create_task`` launch has a
    running loop; the blocking prep runs on a worker thread so a first-run runtime
    materialisation never stalls the event loop. The attach is best-effort — a
    scheduling error is logged and the (already-registered) run is still returned,
    never a 500 (degrade gracefully, never block the sidecar).
    """
    try:
        prepared = await run_in_threadpool(_prepare_node_gen_run, body)
    except AkmsDispatchError as exc:
        # A chosen agent CLI is not installed — actionable client error, not a
        # server fault. 400 (distinct from the WorkflowNotFoundError → 503 case),
        # naming the missing binary + provider so the user can install it. The
        # sidecar stays up.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BatchNotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except WorkflowNotFoundError as exc:
        # If the bundled `akms.node_extraction` workflow isn't found, the
        # AKMS extras / resources are missing — surface as 503 so the
        # frontend lights up an "AKMS not available" banner rather than a
        # generic 404.
        raise HTTPException(
            status_code=503,
            detail=(
                "AKMS node-generation workflow not available "
                "(resources missing or AKMS extra not installed)"
            ),
        ) from exc
    except PromptResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RunServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc

    attach = prepared["attach"]
    if attach is not None:
        # Reclaim the per-run runtime cwd once the agent terminates (success /
        # failure / cancel) so per-run skill workspaces don't accumulate in the
        # cache (issue #103). Only fires when a per-run dir was actually
        # materialised — never on the shared-cwd fallback.
        on_done = None
        if attach.get("cleanup_cwd"):
            def on_done(_record, _cwd=attach["cwd"]):
                import shutil

                shutil.rmtree(_cwd, ignore_errors=True)

        # Launch the executor on the running loop. Best-effort: a scheduling
        # failure (e.g. the run vanished, or the runtime dir is unusable) must
        # not 500 — the run record already exists and the driver flips it to a
        # terminal status on spawn failure.
        try:
            get_registry().attach_workflow_subprocess(
                attach["run_id"],
                args=attach["args"],
                cwd=attach["cwd"],
                log_path=attach["log_path"],
                env=attach["env"],
                stdin=attach["stdin"],
                on_done=on_done,
            )
        except Exception as exc:  # noqa: BLE001 — never 500 on a launch hiccup
            logger.warning(
                "node-gen executor attach failed for run %s: %s",
                attach["run_id"],
                exc,
            )
            # Never leave the run wedged in forever-pending when attach fails
            # (the GUI was stuck here when the generic path registered without
            # launching — same failure mode for a scheduling hiccup).
            _mark_run_failed(attach["run_id"], f"executor attach failed: {exc}")

    return prepared["payload"]


def _mark_run_failed(run_id: str, reason: str) -> None:
    """Best-effort flip of a registered run to terminal ``failed``.

    Used when the subprocess never starts (attach scheduling error). Swallow
    all errors so a secondary failure never 500s the request.
    """
    import datetime as dt

    try:
        reg = get_registry()
        rec = reg.get(run_id)
        if rec is None or rec.status not in ("pending", "running"):
            return
        rec.status = "failed"
        rec.exit_code = -1
        rec.ended_at = dt.datetime.now(dt.UTC).isoformat()
        rec.last_lines.append(f"({reason})")
        # Persist via the private index writer — same path spawn-failure uses.
        reg._persist_index()  # noqa: SLF001 — intentional; no public API yet
        if rec.log_path:
            try:
                Path(rec.log_path).parent.mkdir(parents=True, exist_ok=True)
                with Path(rec.log_path).open("a", encoding="utf-8") as fh:
                    fh.write(f"({reason})\n")
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to mark run %s failed: %s", run_id, exc)


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """Fetch a run record. Same backing store as ``/api/runs/<id>``."""
    rec = get_registry().get(run_id)
    if rec is None:
        raise HTTPException(404, detail=f"run {run_id!r} not found")
    return rec.model_dump()


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> EventSourceResponse:
    """SSE stream for a node-generation run.

    Delegates to :meth:`RunRegistry.stream_workflow_run` (the generic
    workflow-run stream) so the same close-on-EOF behaviour applies — the
    stream terminates when the registry record reaches a terminal status.
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


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, str]:
    """Cancel a running node-generation run.

    Calls into :meth:`RunRegistry.cancel`, which signals the live
    subprocess (the Claude executor process group attached by
    ``attach_workflow_subprocess``). Returns 409 if no
    subprocess is attached yet.
    """
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


__all__ = [
    "AKMS_NODE_EXTRACTION_WORKFLOW",
    "NodeGenerationRunRequest",
    "PreflightRequest",
    "router",
]
