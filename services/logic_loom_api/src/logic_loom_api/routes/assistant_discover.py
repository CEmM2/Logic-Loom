"""``/api/assistant/discover/*`` routes.

Three primary endpoints:

* ``POST /api/assistant/discover/search`` — orchestrates the full pipeline
  (provider search -> normalise -> merge -> cross-ref Zotero). Default
  response shape: ``{"results": [DiscoveredPaperWithMatch], "cached": bool}``.
* ``POST /api/assistant/discover/merge`` — replay: accept pre-normalised
  per-provider lists, return the merged list (no provider calls).
* ``POST /api/assistant/discover/crossref-zotero`` — replay: accept a list
  of DiscoveredPaper, return the same list with ZoteroMatch decorations.

Plus the cache observability endpoints — `GET /cache/stats` and
`DELETE /cache` — wired here so the same router owns the full surface
(stats counters plus the provider version constant).

HARD RULE: ``only_with_pdf`` defaults to ``False`` in the search request
body. Tests pin this.

Cache: per-query payloads land under
``<assistant_workspace>/discovery_cache/<sha256>.json`` with a 24h TTL.
Cache key includes the request body hash AND the
:data:`DISCOVERY_PROVIDER_VERSION` constant — bumping the constant
invalidates every prior entry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..assistant.discovery import (
    DiscoveredPaper,
    PaperDiscoveryService,
    merge_results,
    normalize_openalex,
    normalize_semantic_scholar,
)
from ..assistant.models import ZoteroMatch as ZoteroMatchModel
from ..assistant.providers.errors import (
    ProviderError,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderTimeout,
)
from ..assistant.providers.openalex import ProviderSearchFilters
from ..assistant.related import (
    RANKING_PRESETS,
    RELATED_MODES,
    RELATED_PROVIDER_VERSION,
    RelatedPaperService,
    related_cache_clear,
    related_cache_key,
    related_cache_read,
    related_cache_write,
    related_ttl_for_mode,
)
from ..assistant.zotero_match import ZoteroMatchService
from ..config import Settings
from ..deps import get_settings
from ..errors import http_error

router = APIRouter()

# ---------------------------------------------------------------------------
# Cache plumbing
# ---------------------------------------------------------------------------

#: Cache TTL in seconds. Configurable per call but defaults to 24h.
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60

#: Provider-API contract version. Bump invalidates every existing cache
#: entry (because the version string is part of the cache key).
#: Surfaced via /cache/stats.
DISCOVERY_PROVIDER_VERSION = "v1"

#: Subdirectory under ``<assistant_workspace>`` for the on-disk cache.
DISCOVERY_CACHE_DIRNAME = "discovery_cache"

#: In-process counters for `/cache/stats`. Reset on `/cache` DELETE.
_CACHE_STATS: dict[str, int] = {"hits": 0, "misses": 0, "clears": 0}


def _cache_dir(settings: Settings) -> Path:
    return settings.assistant_workspace_path() / DISCOVERY_CACHE_DIRNAME


def _cache_key(*, body: dict[str, Any], provider_version: str) -> str:
    """Stable cache key = sha256(provider_version + json(sorted-body))."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{provider_version}|{canonical}".encode()).hexdigest()
    return digest


def _cache_path(settings: Settings, key: str) -> Path:
    return _cache_dir(settings) / f"{key}.json"


def _cache_read(
    settings: Settings, key: str, *, ttl_seconds: int
) -> dict[str, Any] | None:
    """Return the cached payload or None on miss/stale/missing."""
    path = _cache_path(settings, key)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    cached_at = payload.get("_cached_at", 0)
    if not isinstance(cached_at, (int, float)):
        return None
    if time.time() - cached_at > ttl_seconds:
        return None
    return payload


def _cache_write(settings: Settings, key: str, payload: dict[str, Any]) -> None:
    """Atomic write of ``payload`` under cache key ``key``.

    Same-dir tempfile + ``os.replace`` for atomicity. Adds the
    ``_cached_at`` epoch timestamp.
    """
    dir_ = _cache_dir(settings)
    dir_.mkdir(parents=True, exist_ok=True)
    path = _cache_path(settings, key)
    enveloped = {**payload, "_cached_at": time.time()}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(enveloped, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------


def get_paper_discovery_service(
    settings: Settings = Depends(get_settings),
) -> PaperDiscoveryService:
    """Build a fresh :class:`PaperDiscoveryService` per request.

    Cheap: provider clients are constructed lazily on first method call.
    Tests override this dep via ``app.dependency_overrides`` to inject
    mocked clients (see ``tests/assistant/test_discovery_routes.py``).
    """
    return PaperDiscoveryService(settings)


def get_zotero_match_service(
    settings: Settings = Depends(get_settings),
) -> ZoteroMatchService:
    """Build a fresh :class:`ZoteroMatchService` per request."""
    return ZoteroMatchService(settings)


def get_related_paper_service(
    settings: Settings = Depends(get_settings),
) -> RelatedPaperService:
    """Build a fresh :class:`RelatedPaperService` per request.

    Reuses the same dep-override pattern as
    :func:`get_paper_discovery_service`; tests in ``tests/assistant/test_related_discovery_routes.py`` swap in
    a stub via ``app.dependency_overrides``.
    """
    return RelatedPaperService(settings)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DiscoverSearchFiltersBody(BaseModel):
    """Filter knobs accepted by /search. Mirrors :class:`ProviderSearchFilters`.

    HARD RULE: ``only_with_pdf`` defaults to ``False``. Test
    `test_only_with_pdf_default_is_false` pins this on the request body.
    """

    model_config = ConfigDict(extra="ignore")

    only_with_pdf: bool = False
    year_from: int | None = None
    year_to: int | None = None
    open_access_only: bool = False
    work_types: list[str] = Field(default_factory=list)


class DiscoverSearchBody(BaseModel):
    """POST /api/assistant/discover/search body."""

    model_config = ConfigDict(extra="ignore")

    query: str
    filters: DiscoverSearchFiltersBody = Field(default_factory=DiscoverSearchFiltersBody)
    limit: int = 25


class DiscoverMergeBody(BaseModel):
    """POST /api/assistant/discover/merge body — replay pre-normalised lists."""

    model_config = ConfigDict(extra="ignore")

    lists: list[list[DiscoveredPaper]] = Field(default_factory=list)


class DiscoverCrossrefBody(BaseModel):
    """POST /api/assistant/discover/crossref-zotero body."""

    model_config = ConfigDict(extra="ignore")

    papers: list[DiscoveredPaper] = Field(default_factory=list)


class DiscoverRelatedFiltersBody(BaseModel):
    """Filter knobs accepted by /related.

    Mirrors :class:`DiscoverSearchFiltersBody`. HARD RULE:
    ``only_with_pdf`` defaults to False.
    """

    model_config = ConfigDict(extra="ignore")

    only_with_pdf: bool = False
    open_access_only: bool = False
    hide_in_zotero: bool = False
    hide_in_batch: bool = False


class DiscoverRelatedBody(BaseModel):
    """POST /api/assistant/discover/related body.

    ``mode`` and ``ranking`` are closed sets —
    enforced by the route handler. ``seed_paper_ids`` defaults to []
    when the caller passes ``batch_id``; the route then expands the
    batch's discovered sources into seeds (UI side; the backend
    accepts both forms).

    The cache key is computed from ``seed_paper_ids`` (sorted) +
    ``mode`` + ``ranking`` so the cache key is stable regardless of
    input ordering.
    """

    model_config = ConfigDict(extra="ignore")

    batch_id: str | None = None
    seed_paper_ids: list[str] = Field(default_factory=list)
    mode: str = "similar"
    ranking: str = "most_relevant"
    limit: int = 25
    year_from: int | None = None
    year_to: int | None = None
    filters: DiscoverRelatedFiltersBody = Field(
        default_factory=DiscoverRelatedFiltersBody
    )


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _map_provider_error(exc: ProviderError) -> HTTPException:
    """Convert a provider error into an HTTP status + structured detail."""
    if isinstance(exc, ProviderRateLimited):
        return HTTPException(
            status_code=429,
            detail={
                "error": "provider_rate_limited",
                "provider": exc.provider,
                "retry_after": exc.retry_after,
                "message": str(exc),
            },
        )
    if isinstance(exc, ProviderNotFound):
        return HTTPException(
            status_code=404,
            detail={
                "error": "provider_not_found",
                "provider": exc.provider,
                "message": str(exc),
            },
        )
    if isinstance(exc, ProviderTimeout):
        return HTTPException(
            status_code=504,
            detail={
                "error": "provider_timeout",
                "provider": exc.provider,
                "message": str(exc),
            },
        )
    return HTTPException(
        status_code=502,
        detail={
            "error": "provider_error",
            "provider": exc.provider,
            "status_code": exc.status_code,
            "message": str(exc),
        },
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/search")
async def discover_search(
    body: DiscoverSearchBody,
    settings: Settings = Depends(get_settings),
    discovery: PaperDiscoveryService = Depends(get_paper_discovery_service),
    zotero: ZoteroMatchService = Depends(get_zotero_match_service),
) -> dict[str, Any]:
    """Full Discovery pipeline: provider search -> merge -> Zotero crossref.

    Caches per-request payloads under ``<assistant_workspace>/discovery_cache/``
    with TTL = :data:`DEFAULT_CACHE_TTL_SECONDS` (24h). Cache key includes
    the body hash AND :data:`DISCOVERY_PROVIDER_VERSION`.
    """
    cache_body = body.model_dump()
    key = _cache_key(body=cache_body, provider_version=DISCOVERY_PROVIDER_VERSION)
    cached = _cache_read(settings, key, ttl_seconds=DEFAULT_CACHE_TTL_SECONDS)
    if cached is not None:
        _CACHE_STATS["hits"] += 1
        return {
            "results": cached.get("results", []),
            "cached": True,
        }

    _CACHE_STATS["misses"] += 1
    filters = ProviderSearchFilters(
        only_with_pdf=body.filters.only_with_pdf,
        year_from=body.filters.year_from,
        year_to=body.filters.year_to,
        open_access_only=body.filters.open_access_only,
        work_types=list(body.filters.work_types),
    )
    try:
        merged = await discovery.search(body.query, filters=filters, limit=body.limit)
    except ProviderError as exc:
        raise _map_provider_error(exc) from exc

    matches = zotero.match_many(merged)
    results = [
        {**paper.model_dump(), "zotero_match": match.model_dump()}
        for paper, match in zip(merged, matches, strict=True)
    ]
    _cache_write(settings, key, {"results": results})
    return {"results": results, "cached": False}


@router.post("/merge")
def discover_merge(body: DiscoverMergeBody) -> dict[str, Any]:
    """Replay endpoint — merge already-normalised per-provider lists."""
    merged = merge_results(*body.lists)
    return {"results": [p.model_dump() for p in merged]}


@router.post("/crossref-zotero")
def discover_crossref_zotero(
    body: DiscoverCrossrefBody,
    zotero: ZoteroMatchService = Depends(get_zotero_match_service),
) -> dict[str, Any]:
    """Replay endpoint — decorate a list of :class:`DiscoveredPaper` with
    :class:`ZoteroMatch`. No provider calls."""
    matches = zotero.match_many(body.papers)
    results = [
        {**paper.model_dump(), "zotero_match": match.model_dump()}
        for paper, match in zip(body.papers, matches, strict=True)
    ]
    return {"results": results}


# ---------------------------------------------------------------------------
# Related-discovery route
# ---------------------------------------------------------------------------


def _resolve_related_mode(mode: str) -> str:
    """Validate ``mode`` against the closed set of related-discovery modes.

    Raises HTTP 400 (via :func:`logic_loom_api.errors.http_error`) with
    the documented allowed-modes list as ``detail`` when ``mode`` is
    unknown.
    """
    if mode not in RELATED_MODES:
        raise http_error(
            400,
            "unknown_related_mode",
            f"Unknown related-paper mode: {mode!r}",
            detail={"mode": mode, "allowed_modes": list(RELATED_MODES)},
        )
    return mode


def _resolve_related_ranking(ranking: str) -> str:
    """Validate ``ranking`` against the closed set of ranking presets.

    Raises HTTP 400 with the documented allowed-rankings list as
    ``detail`` when ``ranking`` is unknown.
    """
    if ranking not in RANKING_PRESETS:
        raise http_error(
            400,
            "unknown_ranking_preset",
            f"Unknown ranking preset: {ranking!r}",
            detail={
                "ranking": ranking,
                "allowed_rankings": list(RANKING_PRESETS),
            },
        )
    return ranking


@router.post("/related")
async def discover_related(
    body: DiscoverRelatedBody,
    settings: Settings = Depends(get_settings),
    related: RelatedPaperService = Depends(get_related_paper_service),
    zotero: ZoteroMatchService = Depends(get_zotero_match_service),
) -> dict[str, Any]:
    """Related-paper discovery — cache-aware.

    Dispatches to the right :class:`RelatedPaperService` mode method,
    decorates results with the Zotero crossref, and applies the pinned
    filter toggles (only_with_pdf / open_access_only / hide_in_zotero).
    Caches the **post-Zotero-crossref** envelope under
    ``<assistant_workspace>/related_cache/<key>.json``. TTL is per-mode:
    7 days for stable signals (similar / foundational /
    highly_cited_neighbours / review_candidates) and 24h for the
    volatile signal (recent_citing).

    Filters are applied AFTER cache lookup so toggling filter knobs on
    a cached payload still works correctly without re-fetching the
    upstream providers.
    """
    mode = _resolve_related_mode(body.mode)
    ranking = _resolve_related_ranking(body.ranking)
    seeds = list(body.seed_paper_ids)

    # Expand FE-emitted fallback seeds to provider ids
    # BEFORE computing the cache key, so two requests pointing at the
    # same paper share a cache entry regardless of which fallback the
    # FE took.
    #   * ``zotero:<citekey>`` → ``doi:<doi>`` via the BBT library FTS5
    #     index (synchronous local sqlite lookup).
    #   * ``doi:<doi>`` → ``openalex:W…`` via one batched OpenAlex query.
    # Sources from the Zotero picker may carry only a citekey when added
    # via the legacy picker or for Zotero items without a DOI; without
    # these expansions the Related tab silently shows "0 seeds".
    if any(s.startswith("zotero:") for s in seeds):
        try:
            seeds = related.expand_zotero_seeds(seeds)
        except Exception as exc:  # noqa: BLE001 — degrade-gracefully
            logger.warning("zotero seed expansion failed: %s", exc)
    if any(s.startswith("doi:") for s in seeds):
        try:
            seeds = await related.expand_doi_seeds(seeds)
        except Exception as exc:  # noqa: BLE001 — degrade-gracefully
            logger.warning("DOI seed expansion failed: %s", exc)

    # ── Cache lookup ─────────────────────────────────────────────────────
    workspace = settings.assistant_workspace_path()
    cache_key = related_cache_key(
        seed_paper_ids=seeds,
        mode=mode,
        ranking=ranking,
        year_from=body.year_from,
        year_to=body.year_to,
        provider_version=RELATED_PROVIDER_VERSION,
    )
    ttl = related_ttl_for_mode(mode)
    cached = related_cache_read(workspace, cache_key, ttl_seconds=ttl)
    if cached is not None:
        # Apply filter toggles client-of-cache.
        results = _apply_related_filters(cached.get("results", []), body.filters)
        return {
            "results": results,
            "mode": mode,
            "ranking": ranking,
            "cached": True,
        }

    # ── Cache miss — call the service ────────────────────────────────────
    try:
        if mode == "similar":
            papers = await related.find_similar(
                seeds, limit=body.limit, ranking=ranking
            )
        elif mode == "foundational":
            papers = await related.find_foundational(
                seeds, limit=body.limit, ranking=ranking
            )
        elif mode == "recent_citing":
            papers = await related.find_recent_citing(
                seeds,
                year_from=body.year_from,
                year_to=body.year_to,
                limit=body.limit,
                ranking=ranking,
            )
        elif mode == "highly_cited_neighbours":
            papers = await related.find_highly_cited_neighbours(
                seeds, limit=body.limit, ranking=ranking
            )
        else:  # mode == "review_candidates"
            papers = await related.find_review_candidates(
                seeds, limit=body.limit, ranking=ranking
            )
    except ProviderError as exc:
        raise _map_provider_error(exc) from exc

    # Run Zotero crossref over the FULL service result so the cached
    # envelope carries the in_zotero flag for every paper. The filter
    # toggles apply on the rendered result set so toggling them on a
    # cached payload (instant) works exactly the same as on a fresh one.
    matches = zotero.match_many(papers)
    cached_results = [
        {**paper.model_dump(), "zotero_match": match.model_dump()}
        for paper, match in zip(papers, matches, strict=True)
    ]
    related_cache_write(workspace, cache_key, {"results": cached_results})

    results = _apply_related_filters(cached_results, body.filters)
    return {
        "results": results,
        "mode": mode,
        "ranking": ranking,
        "cached": False,
    }


def _apply_related_filters(
    rows: list[dict[str, Any]],
    filters: DiscoverRelatedFiltersBody,
) -> list[dict[str, Any]]:
    """Apply the post-fetch filter toggles to a list of result rows.

    Same toggles as the FE :func:`applyClientFilters` for symmetry.
    ``hide_in_batch`` is a client-only filter (the backend doesn't
    know batch contents) — included in the signature for surface
    completeness but treated as no-op here.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if filters.only_with_pdf and not row.get("pdf_url"):
            continue
        if filters.open_access_only and not row.get("is_open_access"):
            continue
        zm = row.get("zotero_match") or {}
        if filters.hide_in_zotero and zm.get("in_zotero"):
            continue
        out.append(row)
    return out


@router.delete("/related-cache")
def discover_related_cache_clear(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Clear every entry from the related-cache directory.

    Returns ``{"removed": int}`` matching the discovery-cache DELETE
    contract. Per-mode entries (7-day-class + 24h-class) are
    cleared together — the related-cache directory holds entries from
    every mode under the same subdirectory.
    """
    workspace = settings.assistant_workspace_path()
    removed = related_cache_clear(workspace)
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Cache observability (counters / version contract)
# ---------------------------------------------------------------------------


@router.get("/cache/stats")
def discover_cache_stats(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Cache hit/miss counters + oldest-entry timestamp.

    Counters are in-process — they reset when the sidecar restarts. The
    on-disk entry count and oldest-entry timestamp are computed live.
    """
    dir_ = _cache_dir(settings)
    entries = 0
    oldest: float | None = None
    if dir_.is_dir():
        for child in dir_.iterdir():
            if child.suffix == ".json" and child.is_file():
                entries += 1
                mtime = child.stat().st_mtime
                if oldest is None or mtime < oldest:
                    oldest = mtime
    return {
        "hits": _CACHE_STATS["hits"],
        "misses": _CACHE_STATS["misses"],
        "clears": _CACHE_STATS["clears"],
        "entries": entries,
        "oldest_entry_epoch": oldest,
        "provider_version": DISCOVERY_PROVIDER_VERSION,
    }


@router.delete("/cache")
def discover_cache_clear(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Clear every entry from the on-disk cache and reset the counters."""
    dir_ = _cache_dir(settings)
    removed = 0
    if dir_.is_dir():
        for child in dir_.iterdir():
            if child.suffix == ".json" and child.is_file():
                try:
                    child.unlink()
                    removed += 1
                except OSError:
                    pass
    _CACHE_STATS["hits"] = 0
    _CACHE_STATS["misses"] = 0
    _CACHE_STATS["clears"] += 1
    return {"removed": removed}


__all__ = [
    "DEFAULT_CACHE_TTL_SECONDS",
    "DISCOVERY_CACHE_DIRNAME",
    "DISCOVERY_PROVIDER_VERSION",
    "DiscoverRelatedBody",
    "DiscoverRelatedFiltersBody",
    "get_paper_discovery_service",
    "get_related_paper_service",
    "get_zotero_match_service",
    "router",
]
