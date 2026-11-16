"""``/api/assistant/batches/*`` routes — Assistant batch HTTP surface.

Endpoints:

* ``GET    /api/assistant/batches``                     — list
* ``POST   /api/assistant/batches``                     — create
* ``GET    /api/assistant/batches/{batch_id}``          — read
* ``PATCH  /api/assistant/batches/{batch_id}``          — partial update
* ``POST   /api/assistant/batches/{batch_id}/duplicate``
* ``POST   /api/assistant/batches/{batch_id}/archive``
* ``POST   /api/assistant/batches/{batch_id}/sources/add``
* ``POST   /api/assistant/batches/{batch_id}/sources/remove``
* ``POST   /api/assistant/batches/{batch_id}/sources/reorder``
* ``POST   /api/assistant/batches/{batch_id}/send-to-akms``

All persistence goes through
:class:`logic_loom_api.assistant.batches.SourceBatchService`. The routes
are thin: parse the body, dispatch to the service, surface ValueErrors
as 404 (unknown id) or 400 (bad input).

Error envelope: every user-facing failure surfaces as
``HTTPException(status_code={400, 404})`` so the global error handler
in :mod:`errors` wraps it in the uniform JSON envelope.

Concurrency: route handlers do NOT wrap mutations in ``get_state_lock()``
— the FastAPI single-process default means concurrent writes
on ``batches.json`` are exceedingly rare (the desktop client is the
sole caller). If concurrent edits become an issue later, wrap each
mutator in ``with get_state_lock():`` and the service's atomic
write-and-replace handles the rest.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..assistant.batches import SourceBatchService
from ..assistant.models import BatchSourceItem, SourceBatch
from ..assistant.services import get_assistant_services
from ..config import Settings
from ..deps import get_settings

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency provider
# ---------------------------------------------------------------------------


def get_source_batch_service(
    settings: Settings = Depends(get_settings),
) -> SourceBatchService:
    """Return a :class:`SourceBatchService` bound to the active settings.

    Resolves through :func:`assistant.services.get_assistant_services` so
    the service carries the research-index post-write hook (batch edits
    flow into the FTS5 index automatically). Construction is cheap —
    the factory builds a small set of facades, none of which touch
    disk on construction.
    """
    return get_assistant_services(settings).batches


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateBatchBody(BaseModel):
    """``POST /api/assistant/batches`` body.

    ``title`` is required; the id is minted by the service from
    ``slugify_title(title)`` with collision suffixes. Every other field
    mirrors :class:`SourceBatch` so callers can seed a batch with sources
    on creation without a second add-sources call.
    """

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    purpose: str | None = None
    sources: list[BatchSourceItem] = Field(default_factory=list)
    notebook_id: str | None = None
    default_workflow_id: str | None = None
    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)


class PatchBatchBody(BaseModel):
    """``PATCH /api/assistant/batches/{id}`` body — every field optional.

    ``id``, ``sources``, and ``created_at`` are not patchable (the service
    rejects them at the model layer). Source list mutations go through
    the dedicated ``sources/add|remove|reorder`` routes.
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    description: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    purpose: str | None = None
    notebook_id: str | None = None
    default_workflow_id: str | None = None
    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] | None = None
    akms_node_plan: list[dict[str, Any]] | None = None


class SourcesAddBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sources: list[BatchSourceItem] = Field(default_factory=list)


class SourcesRemoveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ids: list[str] = Field(default_factory=list)


class SourcesReorderBody(BaseModel):
    """``POST /api/assistant/batches/{id}/sources/reorder``.

    ``ordering`` must be a permutation of the current source ids.
    """

    model_config = ConfigDict(extra="ignore")
    ordering: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_or_404(svc: SourceBatchService, batch_id: str) -> SourceBatch:
    """Return the batch with ``batch_id`` or raise HTTP 404."""
    batch = svc.get(batch_id)
    if batch is None:
        raise HTTPException(
            status_code=404,
            detail=f"assistant batch not found: {batch_id!r}",
        )
    return batch


def _serialize(batch: SourceBatch) -> dict[str, Any]:
    return batch.model_dump()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
def list_batches(
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Return every Assistant batch in stored order."""
    return {"batches": [_serialize(b) for b in svc.list_batches()]}


@router.post("")
def create_batch(
    body: CreateBatchBody,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Create a new Assistant batch.

    Returns the newly-created :class:`SourceBatch` as JSON. The id is
    derived from ``slugify_title(body.title)`` with numeric suffixes on
    collision.
    """
    try:
        batch = svc.create(
            title=body.title,
            description=body.description,
            sources=list(body.sources),
            tags=list(body.tags),
            purpose=body.purpose,
            notebook_id=body.notebook_id,
            default_workflow_id=body.default_workflow_id,
            prompt_profile_id=body.prompt_profile_id,
            prompt_overrides=dict(body.prompt_overrides),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(batch)


@router.get("/{batch_id}")
def get_batch(
    batch_id: str,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Return a single Assistant batch by id (404 if absent)."""
    return _serialize(_get_or_404(svc, batch_id))


@router.patch("/{batch_id}")
def patch_batch(
    batch_id: str,
    body: PatchBatchBody,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Patch arbitrary scalar/dict fields on a batch.

    Source-list mutations are NOT accepted here — use the
    ``sources/{add,remove,reorder}`` routes. Unknown fields are silently
    dropped (``ConfigDict(extra="ignore")``).
    """
    _get_or_404(svc, batch_id)
    fields = body.model_dump(exclude_unset=True)
    try:
        updated = svc.update(batch_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(updated)


@router.post("/{batch_id}/duplicate")
def duplicate_batch(
    batch_id: str,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Clone a batch under a fresh id with ``status="draft"``."""
    _get_or_404(svc, batch_id)
    try:
        clone = svc.duplicate(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(clone)


@router.post("/{batch_id}/archive")
def archive_batch(
    batch_id: str,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Set ``status="archived"`` on a batch."""
    _get_or_404(svc, batch_id)
    archived = svc.archive(batch_id)
    return _serialize(archived)


@router.delete("/{batch_id}")
def delete_batch(
    batch_id: str,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Hard-delete a batch (distinct from archive).

    The research-memory index is updated via the eviction hook fired by
    :meth:`SourceBatchService.delete`. Returns the removed record for
    UI confirmation.
    """
    _get_or_404(svc, batch_id)
    removed = svc.delete(batch_id)
    return _serialize(removed)


@router.post("/{batch_id}/sources/add")
def add_sources(
    batch_id: str,
    body: SourcesAddBody,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Append source items to a batch."""
    _get_or_404(svc, batch_id)
    try:
        batch = svc.add_sources(batch_id, list(body.sources))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(batch)


@router.post("/{batch_id}/sources/remove")
def remove_sources(
    batch_id: str,
    body: SourcesRemoveBody,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Drop source items by id."""
    _get_or_404(svc, batch_id)
    try:
        batch = svc.remove_sources(batch_id, list(body.ids))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(batch)


@router.post("/{batch_id}/sources/reorder")
def reorder_sources(
    batch_id: str,
    body: SourcesReorderBody,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, Any]:
    """Permute the source list to match ``ordering``.

    Returns 400 if ``ordering`` is not a permutation of the current
    source ids (partial / extra ids both rejected).
    """
    _get_or_404(svc, batch_id)
    try:
        batch = svc.reorder_sources(batch_id, list(body.ordering))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize(batch)


@router.post("/{batch_id}/send-to-akms")
def send_to_akms(
    batch_id: str,
    svc: SourceBatchService = Depends(get_source_batch_service),
) -> dict[str, str]:
    """Project the Assistant batch onto the AKMS workspace.

    Returns ``{"akms_batch_id": str, "akms_workspace_path": str}``.
    The Assistant batch is unmodified — send-to-akms is a
    one-way, explicit conversion.
    """
    _get_or_404(svc, batch_id)
    try:
        return svc.send_to_akms(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router", "get_source_batch_service"]
