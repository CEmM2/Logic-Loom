"""``/api/assistant/outputs/*`` — filtered output management view.

Filtered view over the shared run registry for runs whose ``workflow_id``
belongs to an Assistant workflow (per the ``ASSISTANT_CATEGORIES`` set
from :mod:`routes.assistant_workflows`). The four endpoints:

* ``GET    /api/assistant/outputs``                      — list filtered runs
* ``GET    /api/assistant/outputs/{run_id}/primary``     — primary output body
* ``POST   /api/assistant/outputs/{run_id}/open-folder`` — absolute run-dir path
* ``POST   /api/assistant/outputs/{run_id}/export``      — zipped run dir

The list is filtered by Assistant workflow membership (we ask the
workflow catalog whether the run's ``workflow_id`` resolves to a
workflow in ``ASSISTANT_CATEGORIES``). If the workflow has since been
archived or removed, we fall back to checking whether the
``workflow_id`` matches the bundled-default naming convention
(``assistant.<slot>``).

Primary output resolution convention: the Assistant run dispatcher does
NOT write a single
``primary_output_path`` field; rather, the run dir contains the four
reproducibility artefacts (``metadata.json``, ``source_snapshot.json``,
``prompt_snapshot/``, ``logs.txt``) plus — after a real executor run —
one or more output files. ``/primary`` returns the contents of
the most-recently-modified ``*.md`` at the run-dir root (excluding the
``prompt_snapshot/`` subdir). If none exists, returns 404.

The route layer remains a thin adapter: registry-level errors map to
404, filesystem errors to 404/500 as appropriate.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..runs.models import RunRecord
from ..runs.registry import get_registry
from . import assistant_workflows as assistant_workflows_routes
from . import workflows as shared_workflows_routes

logger = logging.getLogger(__name__)

router = APIRouter()


_ASSISTANT_ID_PREFIX = "assistant."


def _is_assistant_workflow_id(workflow_id: str | None) -> bool:
    """Return ``True`` if ``workflow_id`` belongs to the Assistant view.

    The check has two arms:

    1. Look the workflow up in the shared catalog and verify its
       ``category`` is in :data:`ASSISTANT_CATEGORIES`.
    2. If the lookup fails (archived / deleted workflow), fall back to
       the bundled-default naming convention: any id starting with
       ``"assistant."`` is an Assistant run.

    The second arm preserves Assistant runs in the listing even after
    their source workflow is archived — the run's reproducibility
    artefacts on disk are the source of truth, and the user must still
    be able to browse them.
    """
    if not workflow_id:
        return False
    wf = shared_workflows_routes._lookup(workflow_id)
    if wf is not None:
        return assistant_workflows_routes._is_assistant(wf)
    return workflow_id.startswith(_ASSISTANT_ID_PREFIX)


def _run_dir_for(record: RunRecord) -> Path | None:
    """Return the run directory derived from ``record.log_path``.

    Workflow runs store ``log_path = <run_dir>/logs.txt`` (per
    :class:`OutputStore` + the Assistant runner). The run dir is the
    parent of the log path. Returns ``None`` if ``log_path`` is unset.
    """
    if not record.log_path:
        return None
    return Path(record.log_path).parent


def _find_primary_output(run_dir: Path) -> Path | None:
    """Pick the most-recently-modified ``*.md`` at the run-dir root.

    Excludes the ``prompt_snapshot/`` subdir (those markdown files are
    prompt snapshots, not executor outputs). Returns ``None`` if no
    matching file exists — the caller surfaces a 404 in that case so
    the FE detail pane can render a "no output yet" state.
    """
    if not run_dir.exists() or not run_dir.is_dir():
        return None
    candidates: list[Path] = []
    for child in run_dir.iterdir():
        if child.is_file() and child.suffix.lower() == ".md":
            candidates.append(child)
    if not candidates:
        return None
    # Most-recently-modified wins.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _require_assistant_run(run_id: str) -> tuple[RunRecord, Path]:
    """Look up ``run_id`` and validate it's an Assistant workflow run.

    Returns ``(record, run_dir)``. Raises ``HTTPException(404)`` if the
    run does not exist, is not a workflow run, is not an Assistant run,
    or has no on-disk directory.
    """
    rec = get_registry().get(run_id)
    if rec is None:
        raise HTTPException(404, detail=f"run not found: {run_id!r}")
    if rec.kind != "workflow":
        raise HTTPException(404, detail=f"run not found: {run_id!r}")
    if not _is_assistant_workflow_id(rec.workflow_id):
        raise HTTPException(404, detail=f"run not found: {run_id!r}")
    run_dir = _run_dir_for(rec)
    if run_dir is None or not run_dir.exists():
        raise HTTPException(
            404, detail=f"run directory not on disk for {run_id!r}"
        )
    return rec, run_dir


# ---------------------------------------------------------------------------
# Routes (static paths declared FIRST — same FastAPI first-match-trap
# convention as routes/runs.py and routes/assistant_workflows.py)
# ---------------------------------------------------------------------------


@router.get("")
@router.get("/")
def list_assistant_outputs() -> dict[str, Any]:
    """List Assistant-family runs (filtered view of the shared registry).

    A mixed run registry returns only runs whose ``workflow_id``
    belongs to an Assistant workflow. The filter is the same closed-set
    membership test :mod:`routes.assistant_workflows` uses, with a
    fallback for runs whose workflow has been archived.
    """
    reg = get_registry()
    all_workflow_runs = reg.list_workflow_runs()
    filtered = [
        rec for rec in all_workflow_runs
        if _is_assistant_workflow_id(rec.workflow_id)
    ]
    return {"runs": [rec.model_dump() for rec in filtered]}


# ---------------------------------------------------------------------------
# Parametric routes (declared AFTER the static routes above)
# ---------------------------------------------------------------------------


@router.get("/{run_id}/primary")
def get_primary_output(run_id: str) -> dict[str, Any]:
    """Return the primary output's contents for an Assistant run.

    Convention: the primary output is the most-recently-modified ``*.md``
    at the run-dir root (excluding ``prompt_snapshot/``). 404 if the
    run dir has no markdown output yet — the FE detail pane renders
    that as "no output yet" without breaking.
    """
    _rec, run_dir = _require_assistant_run(run_id)
    primary = _find_primary_output(run_dir)
    if primary is None:
        raise HTTPException(
            404, detail=f"no primary output available for run {run_id!r}"
        )
    try:
        content = primary.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            500, detail=f"failed to read primary output: {exc}"
        ) from exc
    return {
        "run_id": run_id,
        "filename": primary.name,
        "content": content,
    }


@router.post("/{run_id}/open-folder")
def open_run_folder(run_id: str) -> dict[str, str]:
    """Return the absolute path of the run directory.

    The frontend opens the path via Tauri shell APIs (``revealItemInDir``
    / ``shell.open``); this endpoint only resolves the path.
    """
    _rec, run_dir = _require_assistant_run(run_id)
    return {"path": str(run_dir.resolve())}


@router.post("/{run_id}/export")
def export_run(run_id: str) -> StreamingResponse:
    """Zip the run directory and stream it back.

    The zip contains every reproducibility artefact in the run dir
    (``metadata.json``, ``source_snapshot.json``,
    ``prompt_snapshot/effective_prompts.json`` + per-slot files,
    ``logs.txt``, and any executor output files). The archive's root
    is the ``run_id`` itself so consumers extract into a tidy
    sub-directory.
    """
    _rec, run_dir = _require_assistant_run(run_id)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                arcname = Path(run_id) / path.relative_to(run_dir)
                zf.write(path, arcname=str(arcname))
    buffer.seek(0)

    headers = {
        "Content-Disposition": f'attachment; filename="{run_id}.zip"',
    }
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers=headers,
    )


__all__ = ["router"]
