"""Semantic Scholar async HTTP client.

Wraps two Semantic Scholar Graph API endpoints:

* ``GET /graph/v1/paper/search`` — used by :meth:`SemanticScholarClient.search`.
* ``POST /graph/v1/paper/batch`` style recommendations — used by
  :meth:`SemanticScholarClient.recommendations` (consumed by the related-paper service).

Auth: the optional ``settings.semantic_scholar_api_key`` is sent as
``x-api-key`` when set; without it the public rate-limit pool applies.

Error mapping is identical to :mod:`.openalex`: 429 →
:class:`ProviderRateLimited` (Retry-After int), 404 →
:class:`ProviderNotFound`, other 4xx/5xx → :class:`ProviderError`,
timeouts → :class:`ProviderTimeout`.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...config import Settings
from .errors import (
    ProviderError,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderTimeout,
)
from .openalex import (
    DEFAULT_TIMEOUT,
    ProviderSearchFilters,
    _is_placeholder,
    _parse_retry_after,
)

#: Semantic Scholar base URL — overridable for tests via the constructor.
SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org"

#: Default ``fields`` query value — must include identifiers + metadata for
#: the discovery normaliser. Keep this stable so cache keys are deterministic.
DEFAULT_S2_FIELDS = (
    "paperId,title,abstract,year,authors,externalIds,openAccessPdf,"
    "venue,publicationTypes,publicationDate,citationCount,referenceCount"
)


class SemanticScholarClient:
    """Async HTTP client for the Semantic Scholar Graph API.

    Same lifecycle as :class:`OpenAlexClient`: cheap to construct, accepts
    an injected :class:`httpx.AsyncClient` for testing.
    """

    PROVIDER_NAME = "semantic_scholar"

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str = SEMANTIC_SCHOLAR_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        filters: ProviderSearchFilters | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search Semantic Scholar papers.

        Returns the parsed JSON response (top-level keys include ``data``
        and ``total``). Normalisation into the :class:`DiscoveredPaper`
        shape happens in the discovery service.
        """
        filters = filters or ProviderSearchFilters()
        params = self._build_search_params(query=query, filters=filters, limit=limit)
        return await self._request_json(
            "GET", "/graph/v1/paper/search", params=params, body=None
        )

    async def recommendations(
        self,
        seed_paper_ids: list[str],
        *,
        mode: str = "similar",
        limit: int = 25,
    ) -> dict[str, Any]:
        """Fetch S2 paper recommendations for the given seeds.

        The S2 ``recommendations/v1/papers`` endpoint accepts a request body
        with ``positivePaperIds`` (the seeds) and an optional limit. The
        ``mode`` argument is reserved for the related-paper service to choose between
        similar / foundational / citing flavours; for the v1 API only
        ``similar`` is hot — this method still surfaces the seeds in the
        documented field name.
        """
        if not seed_paper_ids:
            raise ProviderError(
                "seed_paper_ids must not be empty",
                provider=self.PROVIDER_NAME,
            )
        params: dict[str, str] = {
            "fields": DEFAULT_S2_FIELDS,
            "limit": str(max(1, min(limit, 100))),
        }
        body: dict[str, Any] = {
            "positivePaperIds": list(seed_paper_ids),
            "mode": mode,
        }
        return await self._request_json(
            "POST",
            "/recommendations/v1/papers",
            params=params,
            body=body,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_search_params(
        self,
        *,
        query: str,
        filters: ProviderSearchFilters,
        limit: int,
    ) -> dict[str, str]:
        params: dict[str, str] = {
            "query": query,
            "limit": str(max(1, min(limit, 100))),
            "fields": DEFAULT_S2_FIELDS,
        }
        if filters.only_with_pdf:
            params["openAccessPdf"] = ""  # S2 boolean filter — empty value enables.
        if filters.year_from is not None or filters.year_to is not None:
            lo = filters.year_from if filters.year_from is not None else ""
            hi = filters.year_to if filters.year_to is not None else ""
            params["year"] = f"{lo}-{hi}".strip("-") or f"{lo}{hi}"
        if filters.work_types:
            params["publicationTypes"] = ",".join(filters.work_types)
        return params

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        api_key = self._settings.semantic_scholar_api_key
        if api_key and not _is_placeholder(api_key):
            headers["x-api-key"] = api_key
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str],
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = self._build_headers()
        try:
            if self._client is not None:
                resp = await self._client.request(
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
                f"Semantic Scholar request timed out: {exc}",
                provider=self.PROVIDER_NAME,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Semantic Scholar network error: {exc.__class__.__name__}",
                provider=self.PROVIDER_NAME,
            ) from exc

        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            raise ProviderRateLimited(
                f"Semantic Scholar rate limited (retry_after={retry_after}s)",
                provider=self.PROVIDER_NAME,
                retry_after=retry_after,
            )
        if resp.status_code == 404:
            raise ProviderNotFound(
                f"Semantic Scholar {path} not found",
                provider=self.PROVIDER_NAME,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Semantic Scholar HTTP {resp.status_code}",
                provider=self.PROVIDER_NAME,
                status_code=resp.status_code,
            )
        return resp.json()


__all__ = [
    "DEFAULT_S2_FIELDS",
    "SEMANTIC_SCHOLAR_BASE_URL",
    "SemanticScholarClient",
]
