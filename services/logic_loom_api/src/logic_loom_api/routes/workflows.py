"""``/api/workflows/*`` routes — the Workflow Library API.

Endpoints:

* ``GET    /api/workflows``                       — list (built-ins + user)
* ``GET    /api/workflows/{workflow_id}``         — single workflow
* ``POST   /api/workflows``                       — create a user workflow
* ``PUT    /api/workflows/{workflow_id}``         — update a user workflow
* ``POST   /api/workflows/{workflow_id}/duplicate`` — duplicate (built-in OK)
* ``POST   /api/workflows/{workflow_id}/archive`` — archive a user workflow

Storage:

* **Bundled defaults** are read via the bundled-workflow loader. They are
  never written to disk by the routes layer; an "archive a built-in"
  request is rejected with 409 — clients should duplicate first.
* **User workflows** live under
  ``<akms_workspace>/workflow-library/user-workflows/<id>.json``. The
  directory is bootstrapped by the :func:`bootstrap_akms_workspace`
  helper, but we re-create it on first write in case the workspace was
  pruned at runtime.

Status semantics:

* Archived user workflows are filtered from the default ``GET /``
  listing. Pass ``?include_archived=true`` to include them.
* Archiving a workflow is a soft-delete: the JSON file stays on disk
  with ``status="archived"``, so users can un-archive from the UI.

Built-in vs user precedence:

* If a user workflow has the same id as a built-in, the user copy wins
  in the listing and lookups. Duplication of a built-in mints a new id
  (suffix ``-2``, ``-3``, ...) so the original built-in stays addressable.

Error envelope: every user-facing failure surfaces as
``HTTPException(status_code={400,404,409})`` so the global error handler
in :mod:`errors` wraps it in the uniform JSON envelope.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import Settings
from ..workflows import (
    WORKFLOW_SUFFIX,
    WorkflowDefinition,
    dump_workflow_to_dict,
    list_bundled_workflows,
    load_workflow_from_json,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9._]+")


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _user_workflows_dir() -> Path:
    """Return the user-workflows directory (creating it if missing)."""
    s = Settings.load()
    d = s.akms_workspace.expanduser() / "workflow-library" / "user-workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_workflow_path(workflow_id: str) -> Path:
    """Return the on-disk path for a user workflow by id."""
    return _user_workflows_dir() / f"{workflow_id}{WORKFLOW_SUFFIX}"


def _read_user_workflow(workflow_id: str) -> WorkflowDefinition | None:
    """Read a user workflow from disk, or return None if absent."""
    path = _user_workflow_path(workflow_id)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        wf = load_workflow_from_json(text)
    except (OSError, ValidationError, ValueError) as exc:
        logger.warning("skipping malformed user workflow %s: %s", path.name, exc)
        return None
    # Re-stamp storage-side metadata: it isn't part of the §7 schema and
    # the loader doesn't enforce it, but routes need it for filtering.
    wf.source = "user"
    if wf.status not in ("active", "archived"):
        wf.status = "active"
    return wf


def _list_user_workflows() -> list[WorkflowDefinition]:
    """Return all user workflows on disk (any status)."""
    out: list[WorkflowDefinition] = []
    d = _user_workflows_dir()
    try:
        if not d.is_dir():
            return out
        children = sorted(d.iterdir())
    except PermissionError as exc:
        # macOS TCC blocking akms_workspace (often under ~/Documents) — degrade
        # to "no user workflows" so bundled catalog still serves Assistant.
        logger.warning(
            "user-workflows dir unreadable at %s (%s); using bundled only. "
            "Move akms_workspace out of ~/Documents or grant Full Disk Access.",
            d,
            exc,
        )
        return out
    except OSError as exc:
        logger.warning("user-workflows dir list failed at %s: %s", d, exc)
        return out
    for child in children:
        if not child.name.endswith(WORKFLOW_SUFFIX):
            continue
        wf = _read_user_workflow(child.stem)
        if wf is not None:
            out.append(wf)
    return out


def _write_user_workflow(wf: WorkflowDefinition) -> Path:
    """Persist a user workflow to disk. Atomic via tempfile + rename."""
    d = _user_workflows_dir()
    path = d / f"{wf.id}{WORKFLOW_SUFFIX}"
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Persist full model (including status / source) so user-edited state
    # survives restarts. The §7 schema permits extras-ignore so foreign
    # readers are unaffected.
    data = wf.model_dump()
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _delete_user_workflow_file(workflow_id: str) -> None:
    """Best-effort delete of a user workflow file."""
    path = _user_workflow_path(workflow_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("failed to delete user workflow %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Catalog assembly (bundled + user, with user-wins-on-id precedence)
# ---------------------------------------------------------------------------


def _catalog(*, include_archived: bool = False) -> list[WorkflowDefinition]:
    """Return the union of built-ins and user workflows.

    User entries shadow bundled entries with the same id (so a user can
    "patch" a built-in by writing a same-id user workflow). Archived
    entries are filtered unless ``include_archived=True``.
    """
    by_id: dict[str, WorkflowDefinition] = {}
    for wf in list_bundled_workflows():
        by_id[wf.id] = wf
    for wf in _list_user_workflows():
        by_id[wf.id] = wf
    out = [w for w in by_id.values() if include_archived or w.status != "archived"]
    out.sort(key=lambda w: w.id)
    return out


def _lookup(workflow_id: str, *, include_archived: bool = True) -> WorkflowDefinition | None:
    """Find a workflow by id across both stores. User-wins-on-id."""
    user = _read_user_workflow(workflow_id)
    if user is not None:
        if user.status == "archived" and not include_archived:
            return None
        return user
    for wf in list_bundled_workflows():
        if wf.id == workflow_id:
            return wf
    return None


def _slugify_id(value: str) -> str:
    """Slug for synthesising a new workflow id from a name.

    Allows ``.`` so the dot-delimited family scheme survives. Other
    non-alphanumerics become ``-``. Empty / punctuation-only inputs
    raise :class:`ValueError`.
    """
    if not value:
        raise ValueError("cannot slugify empty value")
    s = _SLUG_NON_ALNUM.sub("-", value.strip().lower()).strip("-._")
    if not s:
        raise ValueError(f"value does not produce a usable slug: {value!r}")
    return s


def _mint_unique_id(base: str) -> str:
    """Return ``base`` (or ``base-2``, ``-3``, ...) — unused in the catalog.

    Looks at both bundled and user workflows so a duplicated built-in
    doesn't collide with another built-in.
    """
    taken = {w.id for w in list_bundled_workflows()} | {
        w.id for w in _list_user_workflows()
    }
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateWorkflowRequest(BaseModel):
    """Body for ``POST /api/workflows``.

    Accepts the full :class:`WorkflowDefinition` schema. If ``id`` is
    omitted, the routes layer derives one from ``name``. Storage-side
    metadata (``source``, ``status``) is forced to ``"user"`` / ``"active"``
    regardless of the request body.
    """

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str
    category: str = ""
    description: str = ""
    executor: str
    source_mode: str = "none"
    input_slots: list[str] = Field(default_factory=list)
    output_format: str = "markdown"
    default_output_subdir: str = ""
    prompt_slots: dict[str, str] = Field(default_factory=dict)
    required_external_tools: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Routes (static paths declared FIRST — FastAPI matches by registration
# order, so a parametric ``/{workflow_id}`` route would capture them)
# ---------------------------------------------------------------------------


@router.get("")
@router.get("/")
def list_workflows(
    include_archived: bool = Query(False, description="include archived user workflows"),
) -> dict[str, Any]:
    """List all workflows (built-ins + user).

    Built-in workflows surface even
    when ``<akms_workspace>/workflow-library/user-workflows/`` is empty or
    missing.
    """
    items = [w.model_dump() for w in _catalog(include_archived=include_archived)]
    return {"workflows": items}


@router.post("")
@router.post("/")
def create_workflow(body: CreateWorkflowRequest) -> dict[str, Any]:
    """Create a new user workflow."""
    # Derive id if absent.
    if body.id:
        try:
            wid = _slugify_id(body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        try:
            wid = _slugify_id(body.name)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"cannot derive workflow id from name: {body.name!r}",
            ) from exc

    if _lookup(wid) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"workflow id already exists: {wid!r}",
        )

    payload = body.model_dump()
    payload["id"] = wid
    payload.pop("status", None)
    payload.pop("source", None)
    try:
        wf = WorkflowDefinition.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    wf.source = "user"
    wf.status = "active"
    _write_user_workflow(wf)
    return wf.model_dump()


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str) -> dict[str, Any]:
    """Fetch a single workflow."""
    wf = _lookup(workflow_id)
    if wf is None:
        raise HTTPException(
            status_code=404, detail=f"workflow not found: {workflow_id!r}"
        )
    return wf.model_dump()


@router.put("/{workflow_id}")
def update_workflow(workflow_id: str, body: CreateWorkflowRequest) -> dict[str, Any]:
    """Update an existing user workflow.

    Built-in workflows are read-only — clients must duplicate first. We
    enforce this by checking the existing record's ``source`` field.
    """
    existing = _lookup(workflow_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail=f"workflow not found: {workflow_id!r}"
        )
    if existing.source == "bundled":
        raise HTTPException(
            status_code=409,
            detail=f"cannot edit built-in workflow {workflow_id!r}; duplicate first",
        )
    payload = body.model_dump()
    # Keep the path id authoritative — the body's id field is informational.
    payload["id"] = workflow_id
    payload.pop("status", None)
    payload.pop("source", None)
    try:
        wf = WorkflowDefinition.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    wf.source = "user"
    wf.status = existing.status  # preserve archived flag if present
    _write_user_workflow(wf)
    return wf.model_dump()


@router.post("/{workflow_id}/duplicate")
def duplicate_workflow(workflow_id: str) -> dict[str, Any]:
    """Duplicate a workflow (built-in or user) into a new user workflow.

    The duplicate's id is minted from the source id with a numeric suffix
    so the original stays addressable.
    """
    src = _lookup(workflow_id)
    if src is None:
        raise HTTPException(
            status_code=404, detail=f"workflow not found: {workflow_id!r}"
        )
    new_id = _mint_unique_id(src.id)
    dup = src.model_copy(update={"id": new_id, "source": "user", "status": "active"})
    _write_user_workflow(dup)
    return dup.model_dump()


@router.delete("/{workflow_id}")
def delete_workflow(workflow_id: str) -> dict[str, str]:
    """Delete a user workflow from disk.

    Built-in workflows cannot be deleted (409). Unknown ids return 404.
    The user file is removed atomically; the in-memory listing rebuilds
    from disk on the next request.
    """
    existing = _lookup(workflow_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail=f"workflow not found: {workflow_id!r}"
        )
    if existing.source == "bundled":
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot delete built-in workflow {workflow_id!r}; "
                "duplicate first and delete the duplicate"
            ),
        )
    path = _user_workflow_path(workflow_id)
    try:
        path.unlink()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"workflow not found: {workflow_id!r}"
        ) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "deleted", "workflow_id": workflow_id}


@router.post("/{workflow_id}/archive")
def archive_workflow(workflow_id: str) -> dict[str, Any]:
    """Archive a user workflow.

    Built-in workflows cannot be archived directly — clients must duplicate
    first (the duplicate is a user workflow that can then be archived).
    """
    existing = _lookup(workflow_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail=f"workflow not found: {workflow_id!r}"
        )
    if existing.source == "bundled":
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot archive built-in workflow {workflow_id!r}; "
                "duplicate first and archive the duplicate"
            ),
        )
    archived = existing.model_copy(update={"status": "archived"})
    _write_user_workflow(archived)
    return archived.model_dump()


__all__ = ["router"]
