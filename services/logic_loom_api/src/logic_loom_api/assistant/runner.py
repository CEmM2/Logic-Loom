"""Assistant run dispatcher.

Implements the run-workflow handler:

  1. Resolve the workflow definition (from the bundled or user
     workflow library).
  2. Build the per-run runtime directory at
     ``<assistant_workspace>/assistant/outputs/<batch_id>/<workflow_id>/<run_id>``.
  3. Write the four reproducibility artefacts:
     - ``metadata.json``           — run + workflow metadata.
     - ``source_snapshot.json``    — snapshot of the resolved batch
       :class:`BatchSourceItem` list.
     - ``prompt_snapshot/effective_prompts.json`` — audit map of slot →
       (variant_id, content_hash).
     - ``prompt_snapshot/<slot>.md`` (or ``.txt``) — one file per
       resolved slot.
     - ``logs.txt``                — empty for dry runs; written by the
       executor for real runs.
  4. Register the run in the shared :class:`RunRegistry` with
     ``kind="workflow"`` (never sharing an id namespace with AKMS).
  5. For ``dry_run=True`` stop here.
  6. For ``executor="claude"`` build a claude-binary argv and hand it
     to ``RunRegistry.attach_workflow_subprocess`` (the same channel the
     AKMS dispatcher uses).
  7. For ``executor="notebooklm"`` build an NLM-CLI argv sequence using
     the batch's ``notebook_id`` and the same registry helper.

The reproducibility writer is intentionally split out into pure helper
functions so tests can exercise it directly without spawning a
subprocess. ``start_run`` is the single public entry point.

Note: the run dispatcher does not write user-facing output artefacts —
that's the executor's job. The four artefacts here are the audit /
reproducibility envelope. The Outputs API surfaces them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from ..runs.models import RunRecord
from ..runs.registry import RunRegistry, get_registry
from ..workflows import WorkflowDefinition, list_bundled_workflows
from .batches import SourceBatchService
from .models import SourceBatch
from .research_index.lifecycle import IndexLifecycle
from .research_index.schema import IndexableRecord, compute_content_hash

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants pinned for tests
# ---------------------------------------------------------------------------

# Closed set of supported executors. The bundled
# workflows ship with ``executor="claude"``; user-defined workflows
# may declare ``executor="notebooklm"`` to use the NLM-CLI path.
SUPPORTED_EXECUTORS: frozenset[str] = frozenset({"claude", "notebooklm"})

# Reproducibility artefact filenames.
METADATA_FILENAME = "metadata.json"
SOURCE_SNAPSHOT_FILENAME = "source_snapshot.json"
PROMPT_SNAPSHOT_DIRNAME = "prompt_snapshot"
EFFECTIVE_PROMPTS_FILENAME = "effective_prompts.json"
LOGS_FILENAME = "logs.txt"


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


class RunWorkflowRequest(BaseModel):
    """Body for the Assistant run-workflow handler.

    Fields:

    * ``batch_id`` — the Assistant source batch to run against.
    * ``workflow_id`` — the workflow to dispatch.
    * ``run_label`` — optional human-readable label (not used for
      directory layout; the auto-generated ``run_id`` is the canonical
      handle).
    * ``prompt_overrides`` — slot → override-content. Bypasses the
      bundled-default resolution for the matching slots.
    * ``dry_run`` — if True, the runner writes the four artefacts and
      registers the run with ``status="pending"`` but does NOT spawn
      the executor.
    """

    model_config = ConfigDict(extra="ignore")

    batch_id: str
    workflow_id: str
    run_label: str | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Helpers — pure functions used by both dry-run and real-run paths
# ---------------------------------------------------------------------------


def _new_run_id() -> str:
    """Mint a run id (16 hex chars).

    Symmetric with the AKMS dispatch registry's ``submit`` helper. The
    Assistant run dispatcher never shares its run-id namespace with
    AKMS zsum runs — the :data:`RunRecord.kind` discriminator already
    pins this in the registry layer.
    """
    return uuid.uuid4().hex[:16]


def _output_root(settings: Settings) -> Path:
    """Return the Assistant output root.

    Kind-discriminated run registry: output paths are
    ``<assistant_workspace>/assistant/outputs/...``. The extra
    ``assistant`` segment mirrors the AKMS-bundle's ``akms``
    segment under its own workspace, so the two systems don't collide
    if a single user co-locates the workspaces.
    """
    return settings.assistant_workspace_path() / "assistant" / "outputs"


def run_dir_for(
    settings: Settings, *, batch_id: str, workflow_id: str, run_id: str
) -> Path:
    """Return the per-run directory path (not yet created)."""
    return _output_root(settings) / batch_id / workflow_id / run_id


def _bundled_workflow(workflow_id: str) -> WorkflowDefinition | None:
    """Look up a bundled workflow by id. Returns ``None`` if absent."""
    for wf in list_bundled_workflows():
        if wf.id == workflow_id:
            return wf
    return None


def _read_assistant_default_md(slot_id: str) -> str:
    """Read the bundled default markdown for an Assistant slot.

    Slot id form is ``assistant.<stem>``. Returns the raw markdown
    content, or an empty string if no default file is shipped.
    """
    family, stem = slot_id.split(".", 1)
    if family != "assistant":
        return ""
    pkg = f"logic_loom_api.resources.prompt_library.defaults.assistant.{stem}"
    try:
        root = resources.files(pkg)
    except (ModuleNotFoundError, FileNotFoundError):
        return ""
    candidate = root.joinpath("default.md")
    try:
        if not candidate.is_file():
            return ""
        return candidate.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return ""


def resolve_effective_prompts(
    workflow: WorkflowDefinition,
    *,
    prompt_overrides: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Return ``slot_name → {slot, variant_id, content, content_hash}``.

    Resolution order per slot (highest precedence first):

      1. ``prompt_overrides[<workflow_slot_name>]`` if supplied — the
         request-level override (the UI's "preview effective
         prompts" path).
      2. Bundled default for ``assistant.<stem>`` slots (the six
         Assistant prompt slots).
      3. Empty content (the slot will be present in the snapshot but
         carry an empty string; downstream executor decides how to
         handle).

    The ``content_hash`` is a SHA-256 hex digest prefixed with
    ``"sha256:"`` so diffing across runs is cheap (the
    rerun-comparison flow consumes this).
    """
    overrides = prompt_overrides or {}
    out: dict[str, dict[str, str]] = {}
    for workflow_slot_name, slot_ref in workflow.prompt_slots.items():
        if workflow_slot_name in overrides:
            content = overrides[workflow_slot_name]
            variant_id = "override"
        else:
            content = _read_assistant_default_md(slot_ref)
            variant_id = "default"
        digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        out[workflow_slot_name] = {
            "slot": slot_ref,
            "variant_id": variant_id,
            "content": content,
            "content_hash": digest,
        }
    return out


def write_reproducibility_artefacts(
    run_dir: Path,
    *,
    workflow: WorkflowDefinition,
    batch: SourceBatch,
    request: RunWorkflowRequest,
    effective_prompts: dict[str, dict[str, str]],
    run_id: str,
    settings: Settings,
) -> dict[str, Path]:
    """Write the four reproducibility artefact families.

    Returns a dict mapping artefact name → on-disk path. Caller owns
    the directory.
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. metadata.json
    metadata_path = run_dir / METADATA_FILENAME
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "batch_id": request.batch_id,
        "workflow_id": request.workflow_id,
        "workflow_name": workflow.name,
        "workflow_category": workflow.category,
        "executor": workflow.executor,
        "source_mode": workflow.source_mode,
        "run_label": request.run_label,
        "dry_run": request.dry_run,
        "kind": "workflow",
        "out_dir": str(run_dir),
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # 2. source_snapshot.json
    source_snapshot_path = run_dir / SOURCE_SNAPSHOT_FILENAME
    source_snapshot = {
        "schema_version": 1,
        "batch_id": batch.id,
        "batch_title": batch.title,
        "notebook_id": batch.notebook_id,
        "sources": [item.model_dump() for item in batch.sources],
    }
    source_snapshot_path.write_text(
        json.dumps(source_snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 3. prompt_snapshot/effective_prompts.json + one file per slot.
    prompt_snapshot_dir = run_dir / PROMPT_SNAPSHOT_DIRNAME
    prompt_snapshot_dir.mkdir(parents=True, exist_ok=True)
    effective_path = prompt_snapshot_dir / EFFECTIVE_PROMPTS_FILENAME
    # Compose the audit-only payload (slot → variant_id + content_hash);
    # the actual content lives in the per-slot files alongside.
    audit_payload = {
        "schema_version": 1,
        "run_id": run_id,
        "slots": {
            name: {
                "slot": entry["slot"],
                "variant_id": entry["variant_id"],
                "content_hash": entry["content_hash"],
            }
            for name, entry in effective_prompts.items()
        },
    }
    effective_path.write_text(
        json.dumps(audit_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    per_slot_files: dict[str, Path] = {}
    for workflow_slot_name, entry in effective_prompts.items():
        # File extension follows the workflow's output_format heuristic —
        # markdown for assistant.* slots; .txt for everything else.
        ext = ".md" if entry["slot"].startswith("assistant.") else ".txt"
        slot_file = prompt_snapshot_dir / f"{workflow_slot_name}{ext}"
        slot_file.write_text(entry["content"], encoding="utf-8")
        per_slot_files[workflow_slot_name] = slot_file

    # 4. logs.txt — created empty so SSE consumers + the run dir lister
    # can always find it.
    logs_path = run_dir / LOGS_FILENAME
    logs_path.write_text("", encoding="utf-8")

    artefacts = {
        "metadata": metadata_path,
        "source_snapshot": source_snapshot_path,
        "prompt_snapshot_dir": prompt_snapshot_dir,
        "effective_prompts": effective_path,
        "logs": logs_path,
    }
    artefacts.update({f"prompt_slot:{n}": p for n, p in per_slot_files.items()})
    return artefacts


# ---------------------------------------------------------------------------
# Executor argv builders
# ---------------------------------------------------------------------------


def build_claude_argv(
    *,
    workflow: WorkflowDefinition,
    run_dir: Path,
    claude_binary: str = "claude",
) -> list[str]:
    """Construct a claude-binary argv for the claude executor.

    Mirrors :func:`logic_loom_api.akms_dispatch.build_dispatch_argv`
    shape — the AKMS dispatcher's existing claude path is the
    canonical reference. This is a thin wrapper because the Assistant
    workflows do not carry a ``plan_path`` (they use the bundled
    prompt + the source_snapshot for grounding) so we cannot reuse
    the AKMS builder verbatim.
    """
    return [
        claude_binary,
        "--workflow",
        workflow.id,
        "--out-dir",
        str(run_dir),
    ]


def build_notebooklm_argv(
    *,
    notebook_id: str,
    workflow: WorkflowDefinition,
    run_dir: Path,
    nlm_binary: str = "nlm",
) -> list[str]:
    """Construct an NLM-CLI argv sequence for the notebooklm executor.

    Shape: ``nlm run --notebook <id> --workflow <wid> --out-dir <run_dir>``.
    Pure builder so tests can pin the argv without spawning a
    subprocess.
    """
    return [
        nlm_binary,
        "run",
        "--notebook",
        notebook_id,
        "--workflow",
        workflow.id,
        "--out-dir",
        str(run_dir),
    ]


# ---------------------------------------------------------------------------
# Run service — the public entry point
# ---------------------------------------------------------------------------


class RunnerService:
    """Assistant workflow run dispatcher.

    Handles both executors (``claude`` + ``notebooklm``),
    writes the four reproducibility artefacts, and registers the run
    with ``kind="workflow"`` in the shared registry.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        batches: SourceBatchService | None = None,
        registry: RunRegistry | None = None,
        index_lifecycle: IndexLifecycle | None = None,
    ) -> None:
        self._settings = settings
        # Lazy resolution: the registry is initialised in main.py's
        # lifespan, so callers from the route layer can rely on
        # get_registry() returning a live instance. Tests inject a
        # fresh registry via the constructor.
        self._registry = registry
        self._batches = batches or SourceBatchService(settings)
        # Optional research-memory index hook. When set (production:
        # wired by ``get_assistant_services``), ``start_run`` fires
        # ``on_change`` for ``run_metadata`` at start and for
        # ``assistant_output`` + ``run_metadata`` again on subprocess
        # completion, so newly-finished runs are searchable without a
        # manual rebuild.
        self._index_lifecycle = index_lifecycle

    # Backwards-compat shim for the service-factory tests, which
    # historically probed ``dispatch_workflow``. The new public surface
    # is :meth:`start_run`; the old name forwards.
    def dispatch_workflow(self, workflow_id: str, **opts: Any) -> Any:
        """Compat shim: forwards to ``start_run``.

        Accepts ``batch_id`` + remaining options as kwargs.
        """
        batch_id = opts.pop("batch_id")
        request = RunWorkflowRequest(
            batch_id=batch_id, workflow_id=workflow_id, **opts
        )
        return self.start_run(request)

    def _get_registry(self) -> RunRegistry:
        if self._registry is not None:
            return self._registry
        return get_registry()

    def _notify_run_indexed(
        self,
        *,
        record: RunRecord,
        run_dir: Path,
        include_output: bool,
    ) -> None:
        """Best-effort post-write hook for the research-memory index.

        Fires :meth:`IndexLifecycle.on_change` for the ``run_metadata``
        record (always — ``metadata.json`` exists from the moment
        :func:`write_reproducibility_artefacts` runs) and, when
        ``include_output`` is set, for the ``assistant_output`` record
        (only meaningful after the executor has written its primary
        ``.md``). All failures are logged at WARNING and swallowed.
        """
        if self._index_lifecycle is None:
            return
        batch_id = record.batch_id or ""
        workflow_id = record.workflow_id or ""
        run_id = record.id
        try:
            metadata_path = run_dir / METADATA_FILENAME
            if metadata_path.is_file():
                body = metadata_path.read_text(encoding="utf-8")
                title = f"{workflow_id} / {run_id}"
                self._index_lifecycle.on_change(
                    IndexableRecord(
                        id=f"run_metadata:{batch_id}:{workflow_id}:{run_id}",
                        kind="run_metadata",
                        title=title,
                        body=body,
                        source_path=str(metadata_path.resolve()),
                        batch_id=batch_id,
                        workflow_id=workflow_id,
                        run_id=run_id,
                        content_hash=compute_content_hash(
                            kind="run_metadata", title=title, body=body
                        ),
                    )
                )
            if include_output:
                primary = self._pick_primary_md(run_dir)
                if primary is not None:
                    body = primary.read_text(encoding="utf-8")
                    title = primary.stem
                    self._index_lifecycle.on_change(
                        IndexableRecord(
                            id=f"assistant_output:{batch_id}:{workflow_id}:{run_id}",
                            kind="assistant_output",
                            title=title,
                            body=body,
                            source_path=str(primary.resolve()),
                            batch_id=batch_id,
                            workflow_id=workflow_id,
                            run_id=run_id,
                            content_hash=compute_content_hash(
                                kind="assistant_output", title=title, body=body
                            ),
                        )
                    )
        except Exception as exc:  # noqa: BLE001 — preserve degrade-gracefully
            logger.warning(
                "assistant.runner: index hook failed for run %s: %s",
                run_id,
                exc,
            )

    @staticmethod
    def _pick_primary_md(run_dir: Path) -> Path | None:
        """Most-recently-modified ``*.md`` at the run-dir root, or ``None``.

        Excludes the ``prompt_snapshot/`` subdir's markdowns — those are
        prompt snapshots, not executor outputs. Mirrors
        :func:`routes.assistant_outputs._find_primary_output`.
        """
        if not run_dir.is_dir():
            return None
        candidates = [
            child
            for child in run_dir.iterdir()
            if child.is_file() and child.suffix.lower() == ".md"
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    def start_run(self, request: RunWorkflowRequest) -> dict[str, Any]:
        """Dispatch a workflow run.

        Returns a dict carrying the registry :class:`RunRecord` dump
        and the on-disk run-dir path. The UI and the outputs API map that
        to their preferred response shape.

        Raises :class:`ValueError` on configuration errors (unknown
        workflow, unknown batch, unsupported executor, missing
        notebook_id for notebooklm).
        """
        # 1. Resolve workflow.
        workflow = _bundled_workflow(request.workflow_id)
        if workflow is None:
            raise ValueError(f"workflow not found: {request.workflow_id!r}")

        # Workflow's declared executor is the source of truth; tests
        # exercise both branches.
        executor = workflow.executor
        if executor not in SUPPORTED_EXECUTORS:
            raise ValueError(
                f"unsupported executor: {executor!r} "
                f"(supported: {sorted(SUPPORTED_EXECUTORS)})"
            )

        # 2. Resolve batch.
        batch = self._batches.get(request.batch_id)
        if batch is None:
            raise ValueError(f"batch not found: {request.batch_id!r}")

        # 3. Compose run dir + run id.
        run_id = _new_run_id()
        run_dir = run_dir_for(
            self._settings,
            batch_id=request.batch_id,
            workflow_id=request.workflow_id,
            run_id=run_id,
        )

        # 4. Resolve effective prompts.
        effective = resolve_effective_prompts(
            workflow, prompt_overrides=request.prompt_overrides
        )

        # 5. Write the four reproducibility artefacts.
        artefacts = write_reproducibility_artefacts(
            run_dir,
            workflow=workflow,
            batch=batch,
            request=request,
            effective_prompts=effective,
            run_id=run_id,
            settings=self._settings,
        )

        # 6. Register with the shared registry. Even dry runs get a
        # registry record so the UI can address them by id.
        registry = self._get_registry()
        record = registry.register_workflow_run(
            batch_id=request.batch_id,
            workflow_id=request.workflow_id,
            run_id=run_id,
            log_path=str(artefacts["logs"]),
            cwd=str(run_dir),
        )

        # 6a. Index the run metadata immediately — ``metadata.json``
        # already exists from step 5 and is searchable. The primary
        # output (``assistant_output``) doesn't exist yet for real
        # runs; we re-fire the hook with ``include_output=True`` from
        # the on_done callback after the subprocess terminates.
        self._notify_run_indexed(
            record=record, run_dir=run_dir, include_output=False
        )

        # 7. Dry run? Stop here.
        if request.dry_run:
            return {
                "run": record.model_dump(),
                "run_dir": str(run_dir),
                "dry_run": True,
                "artefacts": {k: str(v) for k, v in artefacts.items()},
            }

        # 8. Spawn the executor. We split the argv builder + the
        # registry attach call so tests can monkeypatch
        # `attach_workflow_subprocess` and assert on argv shape.
        if executor == "claude":
            argv = build_claude_argv(workflow=workflow, run_dir=run_dir)
        else:  # executor == "notebooklm"
            notebook_id = batch.notebook_id
            if not notebook_id:
                raise ValueError(
                    f"batch {batch.id!r} has no notebook_id; required for "
                    "executor='notebooklm'"
                )
            argv = build_notebooklm_argv(
                notebook_id=notebook_id, workflow=workflow, run_dir=run_dir
            )

        # Capture run_dir for the on_done closure; ``record`` already
        # references the registry's RunRecord by reference, so the
        # callback observes the terminal status.
        captured_run_dir = run_dir

        def _on_subprocess_done(finished: RunRecord) -> None:
            """Re-index after the executor writes its primary output."""
            self._notify_run_indexed(
                record=finished,
                run_dir=captured_run_dir,
                include_output=True,
            )

        registry.attach_workflow_subprocess(
            run_id,
            args=argv,
            cwd=run_dir,
            log_path=artefacts["logs"],
            on_done=_on_subprocess_done,
        )

        return {
            "run": record.model_dump(),
            "run_dir": str(run_dir),
            "dry_run": False,
            "artefacts": {k: str(v) for k, v in artefacts.items()},
            "argv": argv,
        }


__all__ = [
    "EFFECTIVE_PROMPTS_FILENAME",
    "LOGS_FILENAME",
    "METADATA_FILENAME",
    "PROMPT_SNAPSHOT_DIRNAME",
    "RunWorkflowRequest",
    "RunnerService",
    "SOURCE_SNAPSHOT_FILENAME",
    "SUPPORTED_EXECUTORS",
    "build_claude_argv",
    "build_notebooklm_argv",
    "resolve_effective_prompts",
    "run_dir_for",
    "write_reproducibility_artefacts",
]
