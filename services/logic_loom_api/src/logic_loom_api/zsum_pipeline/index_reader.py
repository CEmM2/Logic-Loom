"""Read and aggregate ``<cache_dir>/index.json`` written by zsum.

Faithfully ports plugin_dev/src/index-reader.ts so the desktop's Library
panel sees the same shape as the Obsidian plugin. Pure read-only — no
mutation, no fallbacks; if index.json is malformed we return a typed
``IndexReadResult`` so the UI can render an actionable error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

REVIEW_PAPER_TYPES = frozenset({"review", "survey", "meta-analysis"})


class CollectionIndex(BaseModel):
    name: str
    parent: str | None = None
    path: str
    item_count: int
    digest_state: Literal["ok", "stale", "missing"]
    redigest_state: Literal["ok", "stale", "missing"]


class ItemIndex(BaseModel):
    citekey: str
    year: int = 0
    venue: str = ""
    collections: list[str] = []
    card_state: Literal["cached", "pending", "degraded", "no_pdf"]
    paper_type: str | None = None
    citation_count: int | None = None


class ZsumIndex(BaseModel):
    generated_at: str | None = None
    collections: dict[str, CollectionIndex]
    items: dict[str, ItemIndex]


class LibraryStats(BaseModel):
    total_collections: int
    total_papers: int
    cards_cached: int
    cards_pending: int
    cards_degraded: int
    cards_no_pdf: int
    digests_ok: int
    digests_stale: int
    digests_missing: int


class IndexReadResult(BaseModel):
    index: ZsumIndex | None = None
    kind: Literal["ok", "missing", "parse", "invalid", "io"]
    message: str | None = None


def is_review_paper(item: ItemIndex) -> bool:
    return bool(item.paper_type) and item.paper_type in REVIEW_PAPER_TYPES


def read_index_detailed(cache_dir: Path) -> IndexReadResult:
    target = cache_dir / "index.json"
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return IndexReadResult(kind="missing", message=f"index.json not found at {target}")
    except OSError as e:  # pragma: no cover — filesystem-dependent
        return IndexReadResult(kind="io", message=f"unable to read {target}: {e}")
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        return IndexReadResult(kind="parse", message=f"{target}: {e}")
    if not isinstance(parsed, dict) or "collections" not in parsed or "items" not in parsed:
        return IndexReadResult(
            kind="invalid",
            message=f"{target}: missing required keys (collections/items)",
        )
    try:
        idx = ZsumIndex.model_validate(parsed)
    except Exception as e:
        return IndexReadResult(kind="invalid", message=f"{target}: {e}")
    return IndexReadResult(index=idx, kind="ok")


def compute_stats(idx: ZsumIndex) -> LibraryStats:
    s = LibraryStats(
        total_collections=len(idx.collections),
        total_papers=len(idx.items),
        cards_cached=0,
        cards_pending=0,
        cards_degraded=0,
        cards_no_pdf=0,
        digests_ok=0,
        digests_stale=0,
        digests_missing=0,
    )
    for it in idx.items.values():
        attr = f"cards_{it.card_state}"
        setattr(s, attr, getattr(s, attr) + 1)
    for c in idx.collections.values():
        if c.digest_state == "ok":
            s.digests_ok += 1
        elif c.digest_state == "stale":
            s.digests_stale += 1
        else:
            s.digests_missing += 1
    return s
