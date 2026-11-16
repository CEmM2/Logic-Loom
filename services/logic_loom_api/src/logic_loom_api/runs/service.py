"""RunService — orchestrates workflow runs.

This module is the integration seam that wires together:

* **Prompt resolution** — :func:`logic_loom_api.prompts.resolver.resolve_prompts_for_run`
  computes the effective slot→content map from the global profile +
  batch profile + per-run overrides.
* **Editable batches** — :class:`logic_loom_api.batches.BatchStore` provides
  the editable batch the run targets. The batch is loaded read-only
  unless ``save_as_batch_default=True``, in which case the resolved
  prompt overrides are persisted back into the batch's
  ``prompt_overrides`` field via :meth:`BatchStore.save`.
* **Workflow library** — :func:`logic_loom_api.workflows.list_bundled_workflows`
  / user-workflows-on-disk supply the :class:`WorkflowDefinition` that
  selects the executor (claude / zsum), the source mode, and the
  default output directory.
* **Claude runtime** — :func:`logic_loom_api.akms_claude_runtime.materialize_runtime_workspace`
  is invoked with the effective prompts as an overlay so the per-run
  ``.claude/`` tree reflects the resolver's choices. Skipped for
  ``executor="zsum"`` workflows (which use a different runtime).
* **Dispatch** — the actual executor launch is owned by the AKMS dispatch
  module (:mod:`logic_loom_api.akms_dispatch`); this
  service is responsible only for **enqueuing** the executor — start
  is async, so ``start_workflow`` returns the new :class:`RunRecord`
  immediately.

``dry_run=true`` is a short-circuit: resolve, compute the would-be
output_dir, return the effective prompts payload, do NOT touch the runtime
materializer, the run directory, or the executor queue.

Concurrency: the service is called from FastAPI request handlers, one
request per call. No background coroutines are owned by the service itself.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..akms_claude_runtime import materialize_runtime_workspace
from ..batches import BatchStore, EditableBatch
from ..config import Settings
from ..prompts import (
    ActivePromptProfile,
    PromptStore,
    make_store_loaders,
    resolve_prompts_for_run,
)
from ..workflows import (
    WORKFLOW_SUFFIX,
    WorkflowDefinition,
    list_bundled_workflows,
    load_workflow_from_json,
)
from .output_store import LOG_FILENAME, OutputStore, make_run_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


class RunWorkflowRequest(BaseModel):
    """Request body for ``POST /api/runs/workflow``.

    See the module docstring for the semantics of each field.
    """

    model_config = ConfigDict(extra="ignore")

    batch_id: str
    workflow_id: str
    notebook_id: str | None = None
    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    run_label: str | None = None
    save_as_batch_default: bool = False
    out_dir: str | None = None
    force: bool = False
    dry_run: bool = False
    workflow_inputs: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Service-level errors (mapped to HTTPException at the route layer)
# ---------------------------------------------------------------------------


class RunServiceError(Exception):
    """Base class for service-level errors with an HTTP status hint."""

    status_code: int = 400


class BatchNotFoundError(RunServiceError):
    status_code = 404


class WorkflowNotFoundError(RunServiceError):
    status_code = 404


class PromptResolutionError(RunServiceError):
    status_code = 400


# ---------------------------------------------------------------------------
# Service implementation
# ---------------------------------------------------------------------------


class RunService:
    """Orchestrates a single workflow run.

    Constructed per-request with the live :class:`Settings` so test code
    can monkeypatch ``LOGIC_LOOM_AKMS_WORKSPACE`` and have the service
    pick it up automatically. The service is stateless beyond the
    settings snapshot it captures.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()

    # ------------------------------------------------------------------
    # Lookup helpers (lazy — only called when needed)
    # ------------------------------------------------------------------

    def _workspace(self) -> Path:
        return self.settings.akms_workspace.expanduser()

    def _batch_store(self) -> BatchStore:
        return BatchStore.load(self._workspace())

    def _prompt_store(self) -> PromptStore:
        return PromptStore(self._workspace() / "prompt-library")

    def _load_workflow(self, workflow_id: str) -> WorkflowDefinition:
        """Resolve a workflow id against user-workflows + bundled defaults.

        Mirrors the precedence used by ``routes/workflows.py``: a user
        workflow with the same id shadows the bundled default. Raises
        :class:`WorkflowNotFoundError` on miss.
        """
        user_path = (
            self._workspace()
            / "workflow-library"
            / "user-workflows"
            / f"{workflow_id}{WORKFLOW_SUFFIX}"
        )
        if user_path.exists():
            try:
                wf = load_workflow_from_json(user_path.read_text(encoding="utf-8"))
                wf.source = "user"
                return wf
            except (OSError, ValidationError, ValueError) as exc:
                raise PromptResolutionError(
                    f"user workflow {workflow_id!r} failed to load: {exc}"
                ) from exc
        for wf in list_bundled_workflows():
            if wf.id == workflow_id:
                return wf
        raise WorkflowNotFoundError(f"workflow not found: {workflow_id!r}")

    # ------------------------------------------------------------------
    # Run directory layout
    # ------------------------------------------------------------------

    def _resolve_run_dir(
        self,
        *,
        batch: EditableBatch,
        workflow: WorkflowDefinition,
        run_id: str,
        requested: str | None,
    ) -> Path:
        """Compute the per-run output directory.

        Preference order:

        1. ``requested`` (the request body's ``out_dir``) — used as a
           prefix; the canonical ``<batch_id>/<workflow_id>/<run_id>``
           triplet is appended.
        2. The batch's ``generation.default_output_dir`` if set —
           same triplet appended.
        3. :meth:`OutputStore.run_dir_for` against the workspace —
           ``<workspace>/outputs/<batch_id>/<workflow_id>/<run_id>``
           (the canonical output layout).
        """
        ws = self._workspace()
        if requested:
            base = Path(requested).expanduser()
            return base / batch.id / workflow.id / run_id
        if batch.generation.default_output_dir:
            base = Path(batch.generation.default_output_dir).expanduser()
            return base / batch.id / workflow.id / run_id
        return OutputStore.run_dir_for(
            ws,
            batch_id=batch.id,
            workflow_id=workflow.id,
            run_id=run_id,
        )

    # ------------------------------------------------------------------
    # Effective prompt resolution
    # ------------------------------------------------------------------

    def _resolve_effective_prompts(
        self,
        *,
        batch: EditableBatch,
        request_profile_id: str | None,
        request_overrides: dict[str, str],
    ):
        """Compute the EffectivePromptSet for this run.

        Wraps :func:`resolve_prompts_for_run` with concrete
        loaders backed by the workspace :class:`PromptStore` and the
        global active profile on disk.
        """
        store = self._prompt_store()
        try:
            global_profile = store.load_active_profile()
        except FileNotFoundError:
            # First-run UX: no active profile yet -> empty floor.
            global_profile = ActivePromptProfile()
        profile_loader, variant_loader = make_store_loaders(store)
        try:
            return resolve_prompts_for_run(
                batch=batch,
                global_profile=global_profile,
                request_profile_id=request_profile_id,
                request_overrides=request_overrides or None,
                profile_loader=profile_loader,
                variant_loader=variant_loader,
            )
        except (ValueError, FileNotFoundError) as exc:
            raise PromptResolutionError(
                f"prompt resolution failed: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Batch mutation (save_as_batch_default path)
    # ------------------------------------------------------------------

    def _persist_overrides_to_batch(
        self,
        store: BatchStore,
        batch: EditableBatch,
        *,
        request_profile_id: str | None,
        request_overrides: dict[str, str],
    ) -> None:
        """Mutate the batch's prompt config to record this run's choices.

        Used when the request sets ``save_as_batch_default=True``. We merge
        the new overrides into the batch's existing ``prompt_overrides``
        rather than replacing — the user explicitly opted into this run's
        slot choices but we don't want to silently delete unrelated ones.
        ``request_profile_id`` takes precedence if supplied.
        """
        merged = dict(batch.prompt_overrides)
        merged.update(request_overrides)
        new_batch = batch.model_copy(
            update={
                "prompt_overrides": merged,
                "prompt_profile_id": (
                    request_profile_id if request_profile_id is not None
                    else batch.prompt_profile_id
                ),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        store.replace(new_batch)
        store.save(self._workspace())

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def start_workflow(self, req: RunWorkflowRequest) -> dict[str, Any]:
        """Run-workflow handler entry.

        High-level flow:

        1. Load the batch (404 on miss).
        2. Load the workflow (404 on miss).
        3. Resolve the effective prompt set via the prompt resolver.
        4. Compute the per-run output directory.
        5. If ``save_as_batch_default``, write the overrides back into
           the batch JSON.
        6. If ``dry_run=True``, return the preview payload now — no
           runtime materialisation, no executor enqueue.
        7. Else: materialise the Claude runtime overlay (for executor
           ``"claude"`` workflows) and stake out the run dir, then enqueue
           the executor (the dispatch module launches the actual subprocess).

        Returns a dict ready to be serialised by the FastAPI route layer.
        Raises :class:`RunServiceError` subclasses on user-facing failures.
        """
        store = self._batch_store()
        batch = store.get(req.batch_id)
        if batch is None:
            raise BatchNotFoundError(f"batch not found: {req.batch_id!r}")

        workflow = self._load_workflow(req.workflow_id)

        eps = self._resolve_effective_prompts(
            batch=batch,
            request_profile_id=req.prompt_profile_id,
            request_overrides=req.prompt_overrides,
        )

        run_id = make_run_id(req.run_label)
        run_dir = self._resolve_run_dir(
            batch=batch,
            workflow=workflow,
            run_id=run_id,
            requested=req.out_dir,
        )

        # save_as_batch_default mutates the batch JSON regardless of dry_run.
        # This is the only place a run mutates a batch. We honour it even on
        # dry runs so the preview UX matches what the real run would persist.
        if req.save_as_batch_default:
            self._persist_overrides_to_batch(
                store,
                batch,
                request_profile_id=req.prompt_profile_id,
                request_overrides=req.prompt_overrides,
            )

        preview_payload = {
            "run_id": run_id,
            "batch_id": req.batch_id,
            "workflow_id": req.workflow_id,
            "executor": workflow.executor,
            "source_mode": workflow.source_mode,
            "notebook_id": req.notebook_id,
            "run_label": req.run_label,
            "force": req.force,
            "dry_run": req.dry_run,
            "out_dir": str(run_dir),
            "effective_prompts": eps.model_dump(),
            "workflow_inputs": dict(req.workflow_inputs),
            "saved_as_default": req.save_as_batch_default,
        }

        if req.dry_run:
            preview_payload["status"] = "preview"
            return preview_payload

        # Non-dry runs: materialise the runtime + enqueue the executor.
        # The actual executor launch is owned by the dispatch module; here we just
        # set up the runtime workspace and record the intent.
        if workflow.executor == "claude":
            try:
                materialize_runtime_workspace(
                    force=req.force,
                    effective_prompts=None,  # dispatch applies the per-run overlay
                )
            except Exception as exc:  # noqa: BLE001 — degrade-gracefully
                logger.warning(
                    "claude runtime materialisation failed for run %s: %s",
                    run_id,
                    exc,
                )

        # Materialise the canonical output layout + metadata
        # snapshot, then register the run with the registry. ``out_dir`` is
        # honoured by ``_resolve_run_dir`` — when the caller supplies one we
        # honour it as a prefix; otherwise we land under the workspace
        # ``outputs/`` root via OutputStore.
        if req.out_dir:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "prompt_snapshot").mkdir(parents=True, exist_ok=True)
            log_path = run_dir / LOG_FILENAME
            if not log_path.exists():
                log_path.touch()
        else:
            # Re-use OutputStore so the snapshot tree + logs.txt are staked
            # out in a single place. The run_id was already minted above and
            # the path matches OutputStore.run_dir_for, so create_run_dir is
            # idempotent here.
            _, run_dir = OutputStore.create_run_dir(
                self._workspace(),
                batch_id=batch.id,
                workflow_id=workflow.id,
                run_id=run_id,
            )

        # Write the metadata snapshot.
        metadata_payload = {
            "schema_version": 1,
            "run_id": run_id,
            "batch_id": batch.id,
            "workflow_id": workflow.id,
            "run_label": req.run_label,
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
            "executor": workflow.executor,
            "source_mode": workflow.source_mode,
            "notebook_id": req.notebook_id,
            "prompt_profile_id": req.prompt_profile_id,
            "prompt_overrides": dict(req.prompt_overrides),
            "effective_prompts": eps.model_dump(),
            "workflow_inputs": dict(req.workflow_inputs),
            "output_dir": str(run_dir),
            "saved_as_default": req.save_as_batch_default,
        }
        OutputStore.write_metadata(run_dir, metadata_payload)
        OutputStore.write_prompt_snapshot(run_dir, eps)

        # Register the run in the run registry. Wrapped in a
        # try/except so the run-workflow handler keeps the
        # degrade-gracefully invariant — a registry import / init issue
        # must not crash the run-workflow request.
        try:
            from .registry import get_registry

            registry = get_registry()
            registry.register_workflow_run(
                batch_id=batch.id,
                workflow_id=workflow.id,
                run_id=run_id,
                log_path=str(run_dir / LOG_FILENAME),
                args=[],
                cwd=str(run_dir),
            )
        except Exception as exc:  # noqa: BLE001 — degrade-gracefully
            logger.warning(
                "run registry registration failed for run %s: %s",
                run_id,
                exc,
            )

        preview_payload["status"] = "enqueued"
        return preview_payload


__all__ = [
    "BatchNotFoundError",
    "PromptResolutionError",
    "RunService",
    "RunServiceError",
    "RunWorkflowRequest",
    "WorkflowNotFoundError",
]
