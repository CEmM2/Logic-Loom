"""``/api/assistant/workflows/*`` — Assistant Workflow Library view.

A filtered view over the shared workflow library at
:mod:`logic_loom_api.routes.workflows`. The view is filtered by
``category in ASSISTANT_CATEGORIES`` so
the AKMS "Node Extraction" workflow and AKMS-bundle research/writing
demos do not leak into the Assistant listing — only the six bundled
Assistant workflows (plus any user-created Assistant
workflows) surface here.

Endpoints:

* ``GET    /api/assistant/workflows``                       — filtered list
* ``GET    /api/assistant/workflows/{workflow_id}``         — single workflow (404 if outside Assistant)
* ``POST   /api/assistant/workflows/{workflow_id}/duplicate`` — delegate to shared
* ``POST   /api/assistant/workflows/{workflow_id}/archive`` — delegate to shared

The mutation endpoints (duplicate / archive) are thin delegations to the
shared :mod:`routes.workflows` helpers. They keep the same 4xx semantics:
duplicating a bundled workflow mints a new id; archiving a bundled
workflow returns 409 (clients must duplicate first).

Closed-set category filter pinned by `ASSISTANT_CATEGORIES`; the
constant is the single source of truth for which workflows belong to
the Assistant view. The six bundled Assistant workflows all carry one of these
four categories.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..workflows import WorkflowDefinition
from . import workflows as shared_workflows_routes

logger = logging.getLogger(__name__)

router = APIRouter()

# Closed set of Assistant workflow categories.
ASSISTANT_CATEGORIES: frozenset[str] = frozenset(
    {"Literature Review", "Paper Writing", "Proposal Writing", "Analysis"}
)


def _is_assistant(wf: WorkflowDefinition) -> bool:
    """Return True if ``wf`` belongs to the Assistant view (filtered by category)."""
    return wf.category in ASSISTANT_CATEGORIES


# ---------------------------------------------------------------------------
# Routes (static paths declared FIRST — same FastAPI first-match-trap
# convention as routes/workflows.py)
# ---------------------------------------------------------------------------


@router.get("")
@router.get("/")
def list_assistant_workflows(
    include_archived: bool = Query(
        False, description="include archived user workflows"
    ),
) -> dict[str, Any]:
    """List Assistant workflows (bundled + user, category-filtered).

    Lists the six bundled Assistant workflows. AKMS Node Extraction is NOT
    listed (category="AKMS" is
    outside ``ASSISTANT_CATEGORIES``).
    """
    full_catalog = shared_workflows_routes._catalog(include_archived=include_archived)
    filtered = [wf for wf in full_catalog if _is_assistant(wf)]
    items = [wf.model_dump() for wf in filtered]
    return {"workflows": items}


@router.get("/{workflow_id}")
def get_assistant_workflow(workflow_id: str) -> dict[str, Any]:
    """Fetch a single Assistant workflow.

    Returns 404 if the id is unknown OR if the workflow exists but is
    outside the Assistant category filter — from the Assistant view's
    perspective these are indistinguishable.
    """
    wf = shared_workflows_routes._lookup(workflow_id)
    if wf is None or not _is_assistant(wf):
        raise HTTPException(
            status_code=404,
            detail=f"assistant workflow not found: {workflow_id!r}",
        )
    return wf.model_dump()


@router.post("/{workflow_id}/duplicate")
def duplicate_assistant_workflow(workflow_id: str) -> dict[str, Any]:
    """Duplicate an Assistant workflow.

    Source must be visible to the Assistant view; duplicate inherits
    the source's category so it stays in the Assistant view by
    construction.
    """
    src = shared_workflows_routes._lookup(workflow_id)
    if src is None or not _is_assistant(src):
        raise HTTPException(
            status_code=404,
            detail=f"assistant workflow not found: {workflow_id!r}",
        )
    return shared_workflows_routes.duplicate_workflow(workflow_id)


@router.post("/{workflow_id}/archive")
def archive_assistant_workflow(workflow_id: str) -> dict[str, Any]:
    """Archive a user-side Assistant workflow.

    Delegates to the shared archive endpoint (returns 409 on built-ins).
    Pre-checks that the workflow is in the Assistant view so callers
    cannot archive AKMS / unrelated workflows through this surface.
    """
    existing = shared_workflows_routes._lookup(workflow_id)
    if existing is None or not _is_assistant(existing):
        raise HTTPException(
            status_code=404,
            detail=f"assistant workflow not found: {workflow_id!r}",
        )
    return shared_workflows_routes.archive_workflow(workflow_id)


@router.delete("/{workflow_id}")
def delete_assistant_workflow(workflow_id: str) -> dict[str, str]:
    """Hard-delete a user-side Assistant workflow.

    Same scoping guard as archive: built-ins return 409, non-assistant
    workflows return 404. Delegates to the shared delete handler so the
    on-disk file is removed atomically.
    """
    existing = shared_workflows_routes._lookup(workflow_id)
    if existing is None or not _is_assistant(existing):
        raise HTTPException(
            status_code=404,
            detail=f"assistant workflow not found: {workflow_id!r}",
        )
    return shared_workflows_routes.delete_workflow(workflow_id)


__all__ = ["ASSISTANT_CATEGORIES", "router"]
