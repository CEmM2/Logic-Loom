"""``/api/assistant/index/*`` — research-memory index lifecycle.

Three endpoints under ``/api/assistant/index``:

* ``POST /rebuild`` — walks every workspace artefact, clears the
  index, and re-ingests via the seven adapters.
* ``GET  /status`` — returns per-kind counts + last-rebuild timestamp.
* ``POST /search`` — full-text search against the FTS5 backend, used by
  the FE WorkspaceSearch (Cmd-K) modal.

The lifecycle owner is a process-wide singleton (one
:class:`IndexLifecycle` per :class:`Settings` instance). The dependency
provider here caches that singleton by the settings id; tests inject a
fresh settings via ``get_settings.cache_clear()`` between cases.

Note on incremental hooks: the on_change / on_delete wiring with
BatchStore + run output store fires from the data layer, not from this
route module. We expose :func:`get_index_lifecycle` so the data-layer
callers can grab the same singleton and call ``on_change`` /
``on_delete`` directly.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..assistant.research_index.lifecycle import (
    IndexLifecycle,
    get_lifecycle_for,
    reset_lifecycle_registry,
)
from ..assistant.research_index.schema import INDEX_KINDS
from ..config import Settings
from ..deps import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


def get_index_lifecycle(
    settings: Settings = Depends(get_settings),
) -> IndexLifecycle:
    """Return the process-wide :class:`IndexLifecycle` for ``settings``.

    Delegates to :func:`assistant.research_index.lifecycle.get_lifecycle_for`
    so HTTP callers and data-layer batch-write hooks share the same
    singleton (and therefore the same debounce buffer + on-disk
    ``index.meta.json``).
    """
    return get_lifecycle_for(settings)


def reset_index_lifecycle() -> None:
    """Drop the shared lifecycle registry. Use in tests."""
    reset_lifecycle_registry()


@router.post("/rebuild")
def rebuild_index(
    lifecycle: IndexLifecycle = Depends(get_index_lifecycle),
) -> dict[str, Any]:
    """Trigger a full rebuild of the research-memory index.

    Walks every workspace artefact via the seven adapters and upserts
    every record into the FTS5 backend (clearing the prior on-disk
    rows first). Returns the per-kind counts after the rebuild plus
    the new ``last_rebuild_at`` timestamp.
    """
    counts = lifecycle.rebuild()
    total = sum(counts.values())
    return {
        "counts": counts,
        "total": total,
        "last_rebuild_at": lifecycle.status()["last_rebuild_at"],
    }


@router.get("/status")
def index_status(
    lifecycle: IndexLifecycle = Depends(get_index_lifecycle),
) -> dict[str, Any]:
    """Return the current research-memory index status.

    Shape: ``{counts: {kind: int}, total: int, last_rebuild_at: str | null}``.
    Every closed-set kind is keyed in ``counts`` (zero when absent).
    """
    return lifecycle.status()


class SearchRequest(BaseModel):
    """Request shape for ``POST /api/assistant/index/search``.

    ``query`` is the FTS5 MATCH expression; bare-word queries are
    accepted as-is. ``kinds`` restricts the result set to the listed
    artefact kinds — an empty/missing list searches every kind. Unknown
    kinds are dropped silently rather than raising.
    """

    query: str = Field(..., min_length=1)
    kinds: list[str] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)


@router.post("/search")
def search_index(
    payload: SearchRequest,
    lifecycle: IndexLifecycle = Depends(get_index_lifecycle),
) -> dict[str, Any]:
    """Full-text search the research-memory index.

    Shape: ``{results: [IndexableRecord-dict], total: int}``. ``total``
    is the count of records returned (capped by ``limit``), not a
    server-wide hit count — FTS5 does not pre-compute total matches
    cheaply and the FE only needs the rendered slice.
    """
    valid_kinds = [k for k in payload.kinds if k in INDEX_KINDS]
    records = lifecycle.search(
        payload.query, kinds=valid_kinds or None, limit=payload.limit
    )
    return {
        "results": [r.model_dump() for r in records],
        "total": len(records),
    }


__all__ = [
    "SearchRequest",
    "get_index_lifecycle",
    "reset_index_lifecycle",
    "router",
]
