"""``/api/assistant/zotero/*`` — Zotero picker backend.

Two endpoints that back the Assistant batches Zotero picker:

* ``GET /search`` — sectioned hybrid search.
  * **Library** section comes from a dedicated FTS5 index built from the
    Better-BibTeX JSON export (see
    :mod:`logic_loom_api.assistant.zotero_library_index`). This is the
    full Zotero library — every item the user has, regardless of
    whether a paper card has been generated for it.
  * **Already-processed** section comes from the existing assistant FTS
    index filtered by ``kind="zsum_card"``. Card titles look like
    ``@citekey``; we strip the prefix and join back to the BBT library
    so the row carries title / authors / year just like the Library
    section.

* ``GET /preview/{citekey}`` — single-item detail used by the right-side
  preview drawer. Returns BBT metadata + the rendered card body when one
  exists.

* ``POST /reindex`` — explicit BBT rebuild. The search endpoint already
  rebuilds when the BBT file mtime changes; this hook is for users who
  notice the picker is stale after editing BBT.

CLAUDE.md degrade-gracefully invariant: every endpoint returns 200 with
an explanatory ``reason`` field when the library is unavailable rather
than crashing — the picker UI surfaces a banner instead of an error.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..assistant.research_index.lifecycle import IndexLifecycle, get_lifecycle_for
from ..assistant.zotero_library_index import (
    ZoteroLibraryIndex,
    ZoteroLibraryItem,
    ensure_built,
)
from ..config import Settings
from ..deps import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PickerHit(BaseModel):
    """A row in the picker result list.

    The same shape is used for both sections so the UI can render rows
    uniformly. ``section`` carries the origin for diagnostic UI ("found
    in library", "already in your cards").
    """

    citekey: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    abstract: str = ""
    doi: str = ""
    has_pdf: bool = False
    section: str = "library"  # "library" | "card"
    card_path: str | None = None


class PickerSearchEnvelope(BaseModel):
    """Search response envelope.

    Reports the library-availability ``reason`` (None when healthy) plus
    a flat ``items`` list. Sections are inferred from each item's
    ``section`` field; the UI groups client-side.
    """

    items: list[PickerHit]
    library_count: int
    card_count: int
    library_reason: str | None = None
    bbt_path: str | None = None


class PickerPreview(BaseModel):
    """Preview drawer payload — BBT metadata + card body when present."""

    citekey: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    abstract: str = ""
    doi: str = ""
    url: str = ""
    has_pdf: bool = False
    card_path: str | None = None
    card_body: str | None = None
    library_reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_citekey(card_title: str) -> str:
    """``@smith2024xyz`` → ``smith2024xyz``. Leaves other titles alone."""
    return card_title[1:] if card_title.startswith("@") else card_title


def _items_to_hits(
    items: list[ZoteroLibraryItem], *, section: str
) -> list[PickerHit]:
    return [
        PickerHit(
            citekey=item.citekey,
            title=item.title or item.citekey,
            authors=item.authors,
            year=item.year,
            venue=item.venue,
            abstract=item.abstract,
            doi=item.doi,
            has_pdf=item.has_pdf,
            section=section,
        )
        for item in items
    ]


def _resolve_card_path(settings: Settings, citekey: str) -> Path | None:
    """Return the paper card path for ``citekey`` if it exists on disk."""
    if settings.vault_path is None:
        return None
    candidate = settings.vault_path.expanduser() / "Papers" / f"@{citekey}.md"
    return candidate if candidate.is_file() else None


def _card_hits(
    lifecycle: IndexLifecycle,
    library: ZoteroLibraryIndex,
    *,
    query: str,
    settings: Settings,
    limit: int,
) -> list[PickerHit]:
    """Return Already-processed rows enriched with BBT metadata when possible."""
    # Empty query is special-cased: lifecycle.search uses FTS5 which
    # needs at least one token. Treat blank as "no card hits" — the
    # Library section already shows newest-first, and the UI prompts
    # the user to type something to surface cards.
    if not query.strip():
        return []
    try:
        records = lifecycle.search(query, kinds=["zsum_card"], limit=limit)
    except Exception as exc:  # noqa: BLE001 — degrade-gracefully
        logger.warning("assistant index search failed: %s", exc)
        return []
    hits: list[PickerHit] = []
    for rec in records:
        citekey = _strip_citekey(rec.title)
        bbt_item = library.get(citekey)
        if bbt_item is not None:
            hit = PickerHit(
                citekey=citekey,
                title=bbt_item.title or citekey,
                authors=bbt_item.authors,
                year=bbt_item.year,
                venue=bbt_item.venue,
                abstract=bbt_item.abstract,
                doi=bbt_item.doi,
                has_pdf=bbt_item.has_pdf,
                section="card",
                card_path=rec.source_path or None,
            )
        else:
            # No BBT match — fall back to the card-stem citekey so the
            # row still renders something useful.
            hit = PickerHit(
                citekey=citekey,
                title=citekey,
                section="card",
                card_path=rec.source_path or None,
            )
        hits.append(hit)
    return hits


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/search", response_model=PickerSearchEnvelope)
def search_picker(
    q: str = Query("", description="Free-text query (FTS5 prefix-match)."),
    collection: str | None = Query(
        None, description="Restrict library hits to this BBT collection key."
    ),
    limit: int = Query(40, ge=1, le=200),
    settings: Settings = Depends(get_settings),
) -> PickerSearchEnvelope:
    """Hybrid search across the Zotero library + already-processed cards.

    The endpoint never raises on a missing BBT export — it returns
    ``library_reason`` so the UI can show "Zotero library not
    configured" without breaking the page.
    """
    library, status = ensure_built(settings)
    library_hits = _items_to_hits(
        library.search(query=q, collection_key=collection, limit=limit),
        section="library",
    )
    lifecycle = get_lifecycle_for(settings)
    card_hits = _card_hits(
        lifecycle, library, query=q, settings=settings, limit=limit
    )
    # De-duplicate by citekey across sections: a card row supersedes a
    # library row so the UI doesn't show the same paper twice. Cards
    # come second so we know which to drop.
    card_keys = {hit.citekey for hit in card_hits}
    deduped_library = [hit for hit in library_hits if hit.citekey not in card_keys]
    return PickerSearchEnvelope(
        items=deduped_library + card_hits,
        library_count=len(deduped_library),
        card_count=len(card_hits),
        library_reason=status.get("reason"),
        bbt_path=status.get("bbt_path"),
    )


@router.get("/preview/{citekey}", response_model=PickerPreview)
def get_preview(
    citekey: str,
    settings: Settings = Depends(get_settings),
) -> PickerPreview:
    """Return BBT metadata + card body for ``citekey``.

    404s when the citekey is unknown in *both* the BBT library and the
    paper-cards folder; the UI treats that as "stale link, re-search".
    """
    library, status = ensure_built(settings)
    item = library.get(citekey)
    card_path = _resolve_card_path(settings, citekey)
    if item is None and card_path is None:
        raise HTTPException(status_code=404, detail=f"unknown citekey: {citekey}")
    card_body: str | None = None
    if card_path is not None:
        try:
            card_body = card_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("could not read paper card %s: %s", card_path, exc)
            card_body = None
    return PickerPreview(
        citekey=citekey,
        title=(item.title if item else citekey) or citekey,
        authors=item.authors if item else [],
        year=item.year if item else None,
        venue=item.venue if item else "",
        abstract=item.abstract if item else "",
        doi=item.doi if item else "",
        url=item.url if item else "",
        has_pdf=item.has_pdf if item else False,
        card_path=str(card_path) if card_path else None,
        card_body=card_body,
        library_reason=status.get("reason"),
    )


@router.post("/reindex")
def reindex_library(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Force-rebuild the Zotero library FTS index from the current BBT export."""
    library, status = ensure_built(settings)
    if status.get("reason"):
        return {"rebuilt": False, **status}
    bbt = status.get("bbt_path")
    if not bbt:
        return {"rebuilt": False, "reason": "bbt-path-unset"}
    count = library.build(Path(bbt))
    return {"rebuilt": True, "count": count, **status}


__all__ = ["router"]
