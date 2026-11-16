"""Related-paper recommendation service.

Implements the five related-paper discovery modes:

* :meth:`RelatedPaperService.find_similar` — Semantic Scholar
  ``recommendations`` + OpenAlex ``related_to``.
* :meth:`RelatedPaperService.find_foundational` — OpenAlex / S2
  references graph walk, sorted by citation count.
* :meth:`RelatedPaperService.find_recent_citing` — cited-by graph walk,
  year-range filtered.
* :meth:`RelatedPaperService.find_highly_cited_neighbours` — union of
  related + cited + citing, ranked by ``log(citation_count + 1)``.
* :meth:`RelatedPaperService.find_review_candidates` — title/abstract
  token filter for ``{review, survey, overview, meta-analysis}``,
  weighted by reference count + citation count.

Ranking presets (closed set):

* ``most_relevant`` — semantic_relevance first (input order preserved
  for now; provider responses already encode their own relevance score).
* ``most_cited`` — ``log(citation_count + 1)`` descending.
* ``recent_and_relevant`` — recency bonus on top of relevance.
* ``foundational`` — cited-by-seeds + citation count + older year.

Reuse contract with the discovery service:

* Provider clients are pulled from the injected
  :class:`PaperDiscoveryService` so the polite-pool / api-key wiring
  comes from a single source.
* Normalisation goes through :func:`normalize_openalex` /
  :func:`normalize_semantic_scholar`.
* The merge at the end of every mode uses :func:`merge_results` so
  cross-provider duplicates collapse.

The cache helpers below back the related-discovery route and its DELETE
endpoint.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from .discovery import (
    DiscoveredPaper,
    PaperDiscoveryService,
    merge_results,
    normalize_openalex,
    normalize_semantic_scholar,
)
from .providers.errors import (
    ProviderError,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderTimeout,
)
from .providers.openalex import (
    DEFAULT_TIMEOUT,
    OPENALEX_BASE_URL,
    _is_placeholder,
    _parse_retry_after,
)
from .providers.semantic_scholar import (
    DEFAULT_S2_FIELDS,
    SEMANTIC_SCHOLAR_BASE_URL,
    SemanticScholarClient,
)

#: Closed set of related-paper discovery modes.
RELATED_MODES = (
    "similar",
    "foundational",
    "recent_citing",
    "highly_cited_neighbours",
    "review_candidates",
)

#: Closed set of ranking presets.
RANKING_PRESETS = (
    "most_relevant",
    "most_cited",
    "recent_and_relevant",
    "foundational",
)

#: Tokens that mark a paper as a review candidate.
REVIEW_TOKENS = ("review", "survey", "overview", "meta-analysis")


@dataclass
class RelatedQuery:
    """Bundle of inputs that defines a related-paper lookup.

    Used by the cache helpers — the seed set + mode + ranking is the
    cache key. Year range only matters for the ``recent_citing`` mode;
    the cache key incorporates it via ``year_from`` / ``year_to`` when
    set, since the cached entry's contents depend on it.
    """

    seed_paper_ids: list[str] = field(default_factory=list)
    mode: str = "similar"
    ranking: str = "most_relevant"
    limit: int = 25
    year_from: int | None = None
    year_to: int | None = None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

#: Cache subdirectory under ``<assistant_workspace>``. Separate from
#: the discovery service's ``discovery_cache`` so the two caches can be cleared
#: independently and never collide on keys.
RELATED_CACHE_DIRNAME = "related_cache"

#: Provider-API contract version. Bumping invalidates every existing
#: entry because the version string is part of the cache key. Mirrors
#: the discovery-cache contract.
RELATED_PROVIDER_VERSION = "v1"

#: TTL in seconds for the "stable signal" modes (similar / foundational).
RELATED_TTL_STABLE_SECONDS = 7 * 24 * 60 * 60

#: TTL in seconds for the "volatile signal" mode (recent_citing). The
#: cited-by graph changes as new papers come in, so we re-query daily.
RELATED_TTL_VOLATILE_SECONDS = 24 * 60 * 60

#: highly_cited_neighbours + review_candidates derive from the union of
#: the three primary lookups; we treat them as stable.
_TTL_BY_MODE: dict[str, int] = {
    "similar": RELATED_TTL_STABLE_SECONDS,
    "foundational": RELATED_TTL_STABLE_SECONDS,
    "recent_citing": RELATED_TTL_VOLATILE_SECONDS,
    "highly_cited_neighbours": RELATED_TTL_STABLE_SECONDS,
    "review_candidates": RELATED_TTL_STABLE_SECONDS,
}


def related_ttl_for_mode(mode: str) -> int:
    """Return the TTL in seconds for the given related-paper mode.

    Closed-set lookup; unknown modes fall back to the stable TTL so a
    forgotten mode never accidentally caches forever.
    """
    return _TTL_BY_MODE.get(mode, RELATED_TTL_STABLE_SECONDS)


def related_cache_dir(workspace: Path) -> Path:
    """Return the on-disk cache directory for the active workspace."""
    return workspace / RELATED_CACHE_DIRNAME


def _canonical_key_payload(
    *,
    seed_paper_ids: list[str],
    mode: str,
    ranking: str,
    year_from: int | None,
    year_to: int | None,
) -> dict[str, Any]:
    """Order-independent payload used to derive the cache key.

    Seeds are sorted so identical seed sets produce the same key
    regardless of input ordering — important because two callers
    passing the same papers in different orders should hit the same
    cache entry.
    """
    return {
        "seed_set": sorted(seed_paper_ids),
        "mode": mode,
        "ranking": ranking,
        "year_from": year_from,
        "year_to": year_to,
    }


def related_cache_key(
    *,
    seed_paper_ids: list[str],
    mode: str,
    ranking: str,
    year_from: int | None = None,
    year_to: int | None = None,
    provider_version: str = RELATED_PROVIDER_VERSION,
) -> str:
    """SHA256 cache key — version-aware.

    Bumping :data:`RELATED_PROVIDER_VERSION` invalidates every prior
    entry by changing the key (same contract as the discovery cache).
    """
    payload = _canonical_key_payload(
        seed_paper_ids=seed_paper_ids,
        mode=mode,
        ranking=ranking,
        year_from=year_from,
        year_to=year_to,
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        f"{provider_version}|{canonical}".encode()
    ).hexdigest()


def related_cache_path(workspace: Path, key: str) -> Path:
    """Return the on-disk path for the cache entry with key ``key``."""
    return related_cache_dir(workspace) / f"{key}.json"


def related_cache_read(
    workspace: Path,
    key: str,
    *,
    ttl_seconds: int,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Read a cache entry. Returns ``None`` on miss / stale / unreadable.

    ``now`` is injectable so tests can simulate time travel without
    depending on freezegun.
    """
    path = related_cache_path(workspace, key)
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
    current = time.time() if now is None else now
    if current - cached_at > ttl_seconds:
        return None
    return payload


def related_cache_write(
    workspace: Path,
    key: str,
    payload: dict[str, Any],
    *,
    now: float | None = None,
) -> None:
    """Atomic write of ``payload`` under the cache key.

    Tempfile + ``os.replace`` matches the discovery-cache pattern and
    ``SourceBatchService``.
    """
    dir_ = related_cache_dir(workspace)
    dir_.mkdir(parents=True, exist_ok=True)
    path = related_cache_path(workspace, key)
    enveloped = {
        **payload,
        "_cached_at": time.time() if now is None else now,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(enveloped, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def related_cache_clear(workspace: Path) -> int:
    """Remove every ``*.json`` under the related-cache directory.

    Returns the number of entries deleted (zero if the directory does
    not exist). Tempfiles and other non-json artefacts are left alone.
    """
    dir_ = related_cache_dir(workspace)
    if not dir_.is_dir():
        return 0
    removed = 0
    for child in dir_.iterdir():
        if child.suffix == ".json" and child.is_file():
            try:
                child.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def rank_papers(papers: list[DiscoveredPaper], *, ranking: str) -> list[DiscoveredPaper]:
    """Sort ``papers`` according to the chosen ranking preset.

    Returns a new list (does not mutate the input). Stable sort: papers
    that tie under the chosen preset retain their input order, which
    matters because provider responses already encode relevance in the
    order they return papers.

    Unknown rankings fall back to ``most_relevant`` (input order).
    """
    if ranking == "most_cited":
        return sorted(
            papers,
            key=lambda p: math.log((p.citation_count or 0) + 1),
            reverse=True,
        )
    if ranking == "recent_and_relevant":
        # Newer papers float up among equal-relevance peers; stable sort
        # preserves input order on year ties (relevance).
        indexed = list(enumerate(papers))
        indexed.sort(key=lambda pair: (-(pair[1].year or 0), pair[0]))
        return [p for _, p in indexed]
    if ranking == "foundational":
        # Foundational works are highly cited; among equal-citation peers
        # the older paper wins.
        return sorted(
            papers,
            key=lambda p: (
                math.log((p.citation_count or 0) + 1),
                -(p.year or 9999),
            ),
            reverse=True,
        )
    # most_relevant or unknown — preserve input order.
    return list(papers)


# ---------------------------------------------------------------------------
# Review-candidate filtering
# ---------------------------------------------------------------------------


def is_review_candidate(paper: DiscoveredPaper) -> bool:
    """Return True if any review/survey/overview/meta-analysis token
    appears in the title or abstract (case-insensitive)."""
    haystacks = [paper.title or "", paper.abstract or ""]
    for hay in haystacks:
        lower = hay.lower()
        for token in REVIEW_TOKENS:
            if token in lower:
                return True
    return False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RelatedPaperService:
    """Related-paper recommendation service.

    Constructed per-request via the FastAPI dep wrapper. Reuses the
    injected :class:`PaperDiscoveryService` for provider clients so the
    polite-pool / api-key wiring stays consistent with discovery.

    For tests, all five mode functions accept an injected
    :class:`httpx.AsyncClient` via the constructor; production code
    builds per-request clients on demand.
    """

    OPENALEX_PROVIDER = "openalex"
    SEMANTIC_SCHOLAR_PROVIDER = "semantic_scholar"

    def __init__(
        self,
        settings: Settings,
        *,
        discovery: PaperDiscoveryService | None = None,
        openalex_client: httpx.AsyncClient | None = None,
        semantic_scholar_client: httpx.AsyncClient | None = None,
        openalex_base_url: str = OPENALEX_BASE_URL,
        semantic_scholar_base_url: str = SEMANTIC_SCHOLAR_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._settings = settings
        self._discovery = discovery or PaperDiscoveryService(settings)
        self._openalex_client = openalex_client
        self._semantic_scholar_client = semantic_scholar_client
        self._openalex_base_url = openalex_base_url.rstrip("/")
        self._semantic_scholar_base_url = semantic_scholar_base_url.rstrip("/")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public surface — five modes
    # ------------------------------------------------------------------

    async def find_similar(
        self,
        seed_paper_ids: list[str],
        *,
        limit: int = 25,
        ranking: str = "most_relevant",
    ) -> list[DiscoveredPaper]:
        """Similar-papers mode.

        Calls Semantic Scholar's ``recommendations`` endpoint and
        OpenAlex's ``filter=related_to`` query for the seed papers,
        normalises, merges, ranks. Per-provider failures are logged and
        swallowed so one provider's rate limit doesn't kill the result
        (when the other returned papers).
        """
        s2_seeds = self._extract_s2_seeds(seed_paper_ids)
        oa_seeds = self._extract_openalex_seeds(seed_paper_ids)

        # Track per-provider call outcome separately from result count.
        # An empty result list from a successful call (e.g. OpenAlex
        # honestly has zero related works for the seed) must not be
        # treated as a provider failure — the user should see "no
        # related papers found", not a 502.
        s2_called = False
        s2_failed = False
        s2_list: list[DiscoveredPaper] = []
        if s2_seeds:
            s2_called = True
            try:
                s2_raw = await self._s2_recommendations(s2_seeds, limit=limit)
                s2_list = [
                    normalize_semantic_scholar(entry)
                    for entry in (s2_raw.get("recommendedPapers") or s2_raw.get("data") or [])
                ]
            except ProviderError as exc:
                s2_failed = True
                logger.warning("find_similar: Semantic Scholar failed: %s", exc)

        oa_called = False
        oa_failed = False
        oa_list: list[DiscoveredPaper] = []
        if oa_seeds:
            oa_called = True
            try:
                oa_raw = await self._openalex_related_to(oa_seeds, limit=limit)
                oa_list = [
                    normalize_openalex(entry) for entry in (oa_raw.get("results") or [])
                ]
            except ProviderError as exc:
                oa_failed = True
                logger.warning("find_similar: OpenAlex failed: %s", exc)

        # Raise only when every provider that was actually called blew
        # up. A clean empty response is a legitimate "no results" — not
        # a 502.
        called = [c for c in (s2_called, oa_called) if c]
        failed = sum((s2_failed, oa_failed))
        if called and failed == len(called):
            raise ProviderError(
                "both providers failed for find_similar",
                provider="openalex+semantic_scholar",
            )

        merged = merge_results(s2_list, oa_list)
        merged = self._drop_seeds(merged, seed_paper_ids)
        return rank_papers(merged, ranking=ranking)[:limit]

    async def find_foundational(
        self,
        seed_paper_ids: list[str],
        *,
        limit: int = 25,
        ranking: str = "most_cited",
    ) -> list[DiscoveredPaper]:
        """Foundational-papers mode.

        Walks the references graph of each seed via OpenAlex
        ``referenced_works`` + S2 ``/paper/{id}/references``, normalises,
        merges, ranks (default: most_cited).
        """
        oa_papers = await self._openalex_references_walk(
            self._extract_openalex_seeds(seed_paper_ids), limit=limit
        )
        s2_papers = await self._s2_references_walk(
            self._extract_s2_seeds(seed_paper_ids), limit=limit
        )
        merged = merge_results(oa_papers, s2_papers)
        merged = self._drop_seeds(merged, seed_paper_ids)
        return rank_papers(merged, ranking=ranking)[:limit]

    async def find_recent_citing(
        self,
        seed_paper_ids: list[str],
        *,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 25,
        ranking: str = "recent_and_relevant",
    ) -> list[DiscoveredPaper]:
        """Recent-citing-papers mode.

        Walks the cited-by graph (OpenAlex ``filter=cites:`` + S2
        ``/paper/{id}/citations``), filters by year, normalises,
        merges, ranks.
        """
        oa_papers = await self._openalex_cited_by_walk(
            self._extract_openalex_seeds(seed_paper_ids),
            limit=limit,
            year_from=year_from,
            year_to=year_to,
        )
        s2_papers = await self._s2_citations_walk(
            self._extract_s2_seeds(seed_paper_ids), limit=limit
        )
        # Year-filter S2 results client-side (the API does not filter
        # references by year).
        s2_papers = [
            p
            for p in s2_papers
            if _year_in_range(p.year, year_from=year_from, year_to=year_to)
        ]
        merged = merge_results(oa_papers, s2_papers)
        merged = self._drop_seeds(merged, seed_paper_ids)
        return rank_papers(merged, ranking=ranking)[:limit]

    async def find_highly_cited_neighbours(
        self,
        seed_paper_ids: list[str],
        *,
        limit: int = 25,
        ranking: str = "most_cited",
    ) -> list[DiscoveredPaper]:
        """Highly-cited-neighbours mode.

        Unions related (similar) + cited (foundational) + citing
        (recent_citing), dedups, ranks by ``log(citation_count + 1)``.
        """
        similar = await self.find_similar(seed_paper_ids, limit=limit)
        foundational = await self.find_foundational(seed_paper_ids, limit=limit)
        citing = await self.find_recent_citing(seed_paper_ids, limit=limit)
        merged = merge_results(similar, foundational, citing)
        # No need to drop seeds — each child call already did.
        return rank_papers(merged, ranking=ranking)[:limit]

    async def find_review_candidates(
        self,
        seed_paper_ids: list[str],
        *,
        limit: int = 25,
        ranking: str = "most_cited",
    ) -> list[DiscoveredPaper]:
        """Review/survey-candidates mode.

        Pulls similar + foundational lookups, filters to titles or
        abstracts containing ``{review, survey, overview, meta-analysis}``,
        ranks by reference count then citation count (high reference count
        is the canonical review-paper signal).
        """
        similar = await self.find_similar(seed_paper_ids, limit=max(limit * 2, 50))
        foundational = await self.find_foundational(seed_paper_ids, limit=max(limit * 2, 50))
        candidates = merge_results(similar, foundational)
        candidates = [p for p in candidates if is_review_candidate(p)]
        candidates = self._drop_seeds(candidates, seed_paper_ids)
        # Default ranking for reviews: high reference count then citation count.
        candidates.sort(
            key=lambda p: (
                p.reference_count or 0,
                math.log((p.citation_count or 0) + 1),
            ),
            reverse=True,
        )
        # Allow caller to override with a documented preset.
        if ranking != "most_cited":
            candidates = rank_papers(candidates, ranking=ranking)
        return candidates[:limit]

    # ------------------------------------------------------------------
    # Internal helpers — provider HTTP
    # ------------------------------------------------------------------

    async def _s2_recommendations(
        self, seed_paper_ids: list[str], *, limit: int
    ) -> dict[str, Any]:
        """Call Semantic Scholar ``/recommendations/v1/papers`` for the seeds.

        Reuses :class:`SemanticScholarClient`. The ``mode`` parameter is
        passed through (the client already plumbs it).
        """
        client = SemanticScholarClient(
            self._settings,
            base_url=self._semantic_scholar_base_url,
            timeout=self._timeout,
            client=self._semantic_scholar_client,
        )
        return await client.recommendations(
            seed_paper_ids, mode="similar", limit=limit
        )

    async def _openalex_related_to(
        self, seed_paper_ids: list[str], *, limit: int
    ) -> dict[str, Any]:
        """Call OpenAlex ``/works?filter=related_to:<seed1>|<seed2>...``.

        OpenAlex accepts a pipe-separated id list for the ``related_to``
        filter. Returns the raw ``works`` payload.
        """
        params = self._build_openalex_params(
            filter_clauses=[f"related_to:{ '|'.join(seed_paper_ids) }"],
            per_page=limit,
        )
        return await self._openalex_get("/works", params=params)

    async def _openalex_references_walk(
        self, seed_paper_ids: list[str], *, limit: int
    ) -> list[DiscoveredPaper]:
        """For each seed, fetch its ``referenced_works`` from OpenAlex
        then bulk-fetch those works. Returns a normalised list.

        Practical: pull each seed work to read ``referenced_works``, then
        send a single batched ``filter=ids.openalex:`` query for all
        unique references in one round-trip (per OpenAlex docs, up to 100
        ids per pipe-separated filter).
        """
        if not seed_paper_ids:
            return []
        ref_ids: set[str] = set()
        for seed in seed_paper_ids:
            work = await self._openalex_get(
                f"/works/{seed}", params=self._build_openalex_params()
            )
            for url in work.get("referenced_works", []) or []:
                ref_id = _strip_openalex_url(url)
                if ref_id and ref_id not in seed_paper_ids:
                    ref_ids.add(ref_id)
        if not ref_ids:
            return []
        chunks = list(ref_ids)
        # OpenAlex limit: 100 ids per filter clause.
        out: list[dict[str, Any]] = []
        for i in range(0, len(chunks), 100):
            batch = chunks[i : i + 100]
            params = self._build_openalex_params(
                filter_clauses=[f"ids.openalex:{ '|'.join(batch) }"],
                per_page=min(len(batch), limit),
            )
            payload = await self._openalex_get("/works", params=params)
            out.extend(payload.get("results") or [])
            if len(out) >= limit * 2:
                break
        return [normalize_openalex(entry) for entry in out]

    async def _s2_references_walk(
        self, seed_paper_ids: list[str], *, limit: int
    ) -> list[DiscoveredPaper]:
        """For each seed, fetch ``/paper/{id}/references``. Returns the
        normalised list, deduplicated by paperId within this call."""
        if not seed_paper_ids:
            return []
        out: list[DiscoveredPaper] = []
        seen: set[str] = set()
        for seed in seed_paper_ids:
            payload = await self._s2_get(
                f"/graph/v1/paper/{seed}/references",
                params={
                    "fields": "citedPaper." + DEFAULT_S2_FIELDS.replace(",", ",citedPaper."),
                    "limit": str(min(limit, 100)),
                },
            )
            for row in payload.get("data") or []:
                cited = row.get("citedPaper") or {}
                pid = cited.get("paperId")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                out.append(normalize_semantic_scholar(cited))
        return out

    async def _openalex_cited_by_walk(
        self,
        seed_paper_ids: list[str],
        *,
        limit: int,
        year_from: int | None,
        year_to: int | None,
    ) -> list[DiscoveredPaper]:
        """Fetch OpenAlex works that cite any seed. Single batched query
        via ``filter=cites:<seed1>|<seed2>...``."""
        if not seed_paper_ids:
            return []
        clauses = [f"cites:{ '|'.join(seed_paper_ids) }"]
        if year_from is not None:
            clauses.append(f"from_publication_date:{year_from}-01-01")
        if year_to is not None:
            clauses.append(f"to_publication_date:{year_to}-12-31")
        params = self._build_openalex_params(
            filter_clauses=clauses, per_page=limit
        )
        payload = await self._openalex_get("/works", params=params)
        return [normalize_openalex(entry) for entry in (payload.get("results") or [])]

    async def _s2_citations_walk(
        self, seed_paper_ids: list[str], *, limit: int
    ) -> list[DiscoveredPaper]:
        """For each seed, fetch ``/paper/{id}/citations``. Year filtering
        applies on the caller side because the API does not filter by
        year inline."""
        if not seed_paper_ids:
            return []
        out: list[DiscoveredPaper] = []
        seen: set[str] = set()
        for seed in seed_paper_ids:
            payload = await self._s2_get(
                f"/graph/v1/paper/{seed}/citations",
                params={
                    "fields": "citingPaper." + DEFAULT_S2_FIELDS.replace(",", ",citingPaper."),
                    "limit": str(min(limit, 100)),
                },
            )
            for row in payload.get("data") or []:
                citing = row.get("citingPaper") or {}
                pid = citing.get("paperId")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                out.append(normalize_semantic_scholar(citing))
        return out

    # ------------------------------------------------------------------
    # HTTP plumbing (mirrors openalex.py / semantic_scholar.py)
    # ------------------------------------------------------------------

    def _build_openalex_params(
        self,
        *,
        filter_clauses: list[str] | None = None,
        per_page: int | None = None,
    ) -> dict[str, str]:
        params: dict[str, str] = {}
        if per_page is not None:
            params["per-page"] = str(max(1, min(per_page, 200)))
        email = self._settings.openalex_user_email
        if email and not _is_placeholder(email):
            params["mailto"] = email
        api_key = self._settings.openalex_api_key
        if api_key and not _is_placeholder(api_key):
            params["api_key"] = api_key
        if filter_clauses:
            params["filter"] = ",".join(filter_clauses)
        return params

    def _build_s2_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        api_key = self._settings.semantic_scholar_api_key
        if api_key and not _is_placeholder(api_key):
            headers["x-api-key"] = api_key
        return headers

    async def _openalex_get(
        self, path: str, *, params: dict[str, str]
    ) -> dict[str, Any]:
        url = f"{self._openalex_base_url}{path}"
        return await self._request_json(
            "GET",
            url,
            params=params,
            headers={"Accept": "application/json"},
            body=None,
            client=self._openalex_client,
            provider=self.OPENALEX_PROVIDER,
        )

    async def _s2_get(
        self, path: str, *, params: dict[str, str]
    ) -> dict[str, Any]:
        url = f"{self._semantic_scholar_base_url}{path}"
        return await self._request_json(
            "GET",
            url,
            params=params,
            headers=self._build_s2_headers(),
            body=None,
            client=self._semantic_scholar_client,
            provider=self.SEMANTIC_SCHOLAR_PROVIDER,
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        body: dict[str, Any] | None,
        client: httpx.AsyncClient | None,
        provider: str,
    ) -> dict[str, Any]:
        try:
            if client is not None:
                resp = await client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=body,
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as c:
                    resp = await c.request(
                        method, url, params=params, headers=headers, json=body
                    )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"{provider} request timed out: {exc}",
                provider=provider,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"{provider} network error: {exc.__class__.__name__}",
                provider=provider,
            ) from exc

        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            raise ProviderRateLimited(
                f"{provider} rate limited (retry_after={retry_after}s)",
                provider=provider,
                retry_after=retry_after,
            )
        if resp.status_code == 404:
            raise ProviderNotFound(
                f"{provider} {url} not found",
                provider=provider,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"{provider} HTTP {resp.status_code}",
                provider=provider,
                status_code=resp.status_code,
            )
        return resp.json()

    # ------------------------------------------------------------------
    # Seed-id handling
    # ------------------------------------------------------------------

    def expand_zotero_seeds(self, seed_paper_ids: list[str]) -> list[str]:
        """Resolve ``zotero:<citekey>`` seeds to ``doi:<doi>`` (or drop).

        Companion to :meth:`expand_doi_seeds`. The FE falls back to
        emitting ``zotero:<citekey>`` when a batch source has no
        DOI / provider id, so users with legacy picker rows (or Zotero
        items that simply lack a DOI) don't see "0 seeds" on the Related
        tab. We use the BBT-derived Zotero library FTS5 index (the one
        behind the Zotero picker): each citekey is looked up; the row's DOI is substituted
        when present, otherwise the seed is dropped (logged at INFO).

        Synchronous: the index is a local sqlite query — no HTTP round
        trips, so no need to make this async.
        """
        if not seed_paper_ids:
            return []
        citekeys = [
            sid.split(":", 1)[1].strip()
            for sid in seed_paper_ids
            if sid.startswith("zotero:") and sid.split(":", 1)[1].strip()
        ]
        if not citekeys:
            return list(seed_paper_ids)
        # Late import to avoid a hard dependency cycle: ensure_built lives
        # in the picker module which itself imports related-discovery
        # types transitively. Importing inside the method keeps the
        # module-load graph clean.
        from .zotero_library_index import ensure_built

        try:
            library, _status = ensure_built(self._settings)
        except Exception as exc:  # noqa: BLE001 — degrade-gracefully
            logger.warning(
                "zotero seed expansion: library unavailable (%s); "
                "dropping %d zotero seeds",
                exc,
                len(citekeys),
            )
            return [s for s in seed_paper_ids if not s.startswith("zotero:")]
        out: list[str] = []
        for sid in seed_paper_ids:
            if not sid.startswith("zotero:"):
                out.append(sid)
                continue
            citekey = sid.split(":", 1)[1].strip()
            item = library.get(citekey)
            if item is None:
                logger.info(
                    "related: zotero seed %r unknown in BBT library", citekey
                )
                continue
            if item.doi:
                out.append(f"doi:{item.doi}")
            else:
                logger.info(
                    "related: zotero seed %r has no DOI in BBT", citekey
                )
        return out

    async def expand_doi_seeds(
        self, seed_paper_ids: list[str]
    ) -> list[str]:
        """Resolve ``doi:`` seeds to ``openalex:W…`` seeds via one batched lookup.

        When only ``openalex:`` / ``semantic_scholar:`` seeds were accepted,
        batches assembled from the Zotero picker (where sources usually carry a DOI but no
        provider id) produced an empty seed list and the Related tab
        showed "0 seeds". Accepting DOI seeds lets the typical Zotero
        flow succeed without the user pre-discovering each source.

        Unresolvable DOIs are silently dropped (logged at WARNING).
        Calls OpenAlex once with a pipe-separated ``filter=doi:…`` query
        so the resolution is one HTTP round-trip regardless of how many
        DOI seeds are present.
        """
        if not seed_paper_ids:
            return []
        dois = [
            sid.split(":", 1)[1].strip()
            for sid in seed_paper_ids
            if sid.startswith("doi:") and sid.split(":", 1)[1].strip()
        ]
        if not dois:
            return list(seed_paper_ids)
        resolved: dict[str, str] = {}
        # OpenAlex filter limit is 100 ids per pipe-separated clause.
        for i in range(0, len(dois), 100):
            batch = dois[i : i + 100]
            try:
                payload = await self._openalex_get(
                    "/works",
                    params=self._build_openalex_params(
                        filter_clauses=[f"doi:{ '|'.join(batch) }"],
                        per_page=len(batch),
                    ),
                )
            except ProviderError as exc:
                logger.warning(
                    "DOI seed expansion failed (%s); dropping %d DOI seeds",
                    exc,
                    len(batch),
                )
                continue
            for work in payload.get("results") or []:
                w_url = work.get("id") or ""
                w_id = _strip_openalex_url(w_url)
                w_doi = work.get("doi") or ""
                # OpenAlex returns DOIs as https://doi.org/<doi>.
                if w_doi.startswith("https://doi.org/"):
                    w_doi = w_doi[len("https://doi.org/") :]
                if w_id and w_doi:
                    resolved[w_doi.lower()] = w_id
        out: list[str] = []
        for sid in seed_paper_ids:
            if not sid.startswith("doi:"):
                out.append(sid)
                continue
            doi = sid.split(":", 1)[1].strip().lower()
            w_id = resolved.get(doi)
            if w_id:
                out.append(f"openalex:{w_id}")
            else:
                logger.info("related: DOI seed %r unresolved", doi)
        return out

    def _extract_openalex_seeds(self, seed_paper_ids: list[str]) -> list[str]:
        """Pull the OpenAlex short ids (``W123``) out of canonical seed ids.

        Canonical seed ids are ``<provider>:<provider_id>``; bare
        ``W123`` strings are also accepted.
        """
        out: list[str] = []
        for sid in seed_paper_ids:
            if sid.startswith("openalex:"):
                out.append(sid.split(":", 1)[1])
            elif sid.startswith("W") and sid[1:].isdigit():
                out.append(sid)
        return out

    def _extract_s2_seeds(self, seed_paper_ids: list[str]) -> list[str]:
        """Pull the Semantic Scholar paper ids out of canonical seed ids.

        Only accepts the canonical ``semantic_scholar:<paperId>`` prefix
        or a bare 40-char lowercase hex paperId. Other bare strings are
        ignored so a Zotero key / arxiv id / DOI fallback does not get
        misrouted to S2 (which would return 404 and surface as a 502
        from /api/assistant/discover/related when paired with an empty
        OpenAlex seed set).
        """
        out: list[str] = []
        for sid in seed_paper_ids:
            if sid.startswith("semantic_scholar:"):
                out.append(sid.split(":", 1)[1])
            elif _looks_like_s2_paper_id(sid):
                out.append(sid)
        return out

    def _drop_seeds(
        self, papers: list[DiscoveredPaper], seed_paper_ids: list[str]
    ) -> list[DiscoveredPaper]:
        """Remove any paper whose canonical id or provider id is in the
        seed set (a seed should not appear in its own related list)."""
        seeds = set(seed_paper_ids)
        oa_seeds = set(self._extract_openalex_seeds(seed_paper_ids))
        s2_seeds = set(self._extract_s2_seeds(seed_paper_ids))
        out: list[DiscoveredPaper] = []
        for p in papers:
            if p.id in seeds:
                continue
            if p.openalex_id and p.openalex_id in oa_seeds:
                continue
            if p.semantic_scholar_id and p.semantic_scholar_id in s2_seeds:
                continue
            out.append(p)
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_S2_PAPER_ID_HEX = set("0123456789abcdef")


def _looks_like_s2_paper_id(value: str) -> bool:
    """True if ``value`` is the canonical S2 paperId form (40-char hex)."""
    return len(value) == 40 and all(ch in _S2_PAPER_ID_HEX for ch in value)


def _strip_openalex_url(value: str | None) -> str | None:
    """Strip ``https://openalex.org/`` prefix from a referenced_work URL."""
    if not value:
        return None
    if value.startswith("https://openalex.org/"):
        return value.rsplit("/", 1)[-1]
    return value


def _year_in_range(
    year: int | None,
    *,
    year_from: int | None,
    year_to: int | None,
) -> bool:
    """Inclusive range check; missing year passes unless a bound is set."""
    if year is None:
        # If either bound is set, missing year fails the filter.
        return year_from is None and year_to is None
    if year_from is not None and year < year_from:
        return False
    if year_to is not None and year > year_to:
        return False
    return True


__all__ = [
    "RANKING_PRESETS",
    "RELATED_CACHE_DIRNAME",
    "RELATED_MODES",
    "RELATED_PROVIDER_VERSION",
    "RELATED_TTL_STABLE_SECONDS",
    "RELATED_TTL_VOLATILE_SECONDS",
    "REVIEW_TOKENS",
    "RelatedPaperService",
    "RelatedQuery",
    "is_review_candidate",
    "rank_papers",
    "related_cache_clear",
    "related_cache_dir",
    "related_cache_key",
    "related_cache_path",
    "related_cache_read",
    "related_cache_write",
    "related_ttl_for_mode",
]
