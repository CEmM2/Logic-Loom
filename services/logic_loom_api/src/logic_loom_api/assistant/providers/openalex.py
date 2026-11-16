"""OpenAlex async HTTP client.

Minimal wrapper around ``https://api.openalex.org/works``. Returns the raw
parsed JSON — normalisation into the :class:`DiscoveredPaper` shape is
the discovery service's job.

Key behaviours:

* ``mailto=`` polite-pool param is injected from ``settings.openalex_user_email``
  whenever it is set and non-placeholder.
* HTTP 429 raises :class:`ProviderRateLimited` carrying ``Retry-After`` as
  an integer attribute. The ``Retry-After`` header is parsed using the
  HTTP-date or seconds form (per RFC 7231); on parse failure we fall
  back to ``retry_after=0``.
* HTTP 404 raises :class:`ProviderNotFound`.
* Other 4xx/5xx raise :class:`ProviderError` with the upstream status code.
* :class:`httpx.TimeoutException` raises :class:`ProviderTimeout`.

The client accepts an injected :class:`httpx.AsyncClient` for tests
(MockTransport / respx). When not provided it builds its own per-request
client so callers don't need to manage a session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ...config import Settings
from .errors import (
    ProviderError,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderTimeout,
)

#: Default request timeout for provider calls (seconds).
DEFAULT_TIMEOUT = 15.0

#: OpenAlex base URL — overridable for tests via the constructor.
OPENALEX_BASE_URL = "https://api.openalex.org"


@dataclass
class ProviderSearchFilters:
    """Discovery search filters shared across providers.

    Defaults are pinned: ``only_with_pdf=False`` so search never silently
    drops papers that lack an open-access PDF. Provider and route tests
    assert this default.

    Other filters (year range, open-access only, types) are optional and
    only added to the upstream query when explicitly set.
    """

    only_with_pdf: bool = False
    year_from: int | None = None
    year_to: int | None = None
    open_access_only: bool = False
    work_types: list[str] = field(default_factory=list)


def _parse_retry_after(value: str | None) -> int:
    """Best-effort parse of the ``Retry-After`` header.

    Returns the integer number of seconds. If the header is missing,
    unparseable, or is the (rare) HTTP-date form, returns ``0``. The
    HTTP-date branch would require timezone-aware datetime math; since
    every provider we hit in production uses delta-seconds, accepting
    only that form keeps the implementation simple.
    """
    if value is None:
        return 0
    stripped = value.strip()
    if not stripped:
        return 0
    try:
        return max(0, int(stripped))
    except ValueError:
        return 0


def _is_placeholder(raw: str | None) -> bool:
    """Mirror :func:`logic_loom_api.config.is_placeholder` without an import
    cycle. The TOML template ships values wrapped in ``<>``; treat any such
    value as "not really set"."""
    if raw is None:
        return False
    stripped = raw.strip()
    return stripped.startswith("<") and stripped.endswith(">")


class OpenAlexClient:
    """Async HTTP client for the OpenAlex ``/works`` endpoint.

    Lifecycle: the client is cheap to construct and does not maintain a
    persistent connection pool by default. Pass a pre-built
    :class:`httpx.AsyncClient` via ``client=`` for tests using
    :class:`httpx.MockTransport`.
    """

    PROVIDER_NAME = "openalex"

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str = OPENALEX_BASE_URL,
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
        """Search OpenAlex works.

        Returns the parsed JSON response (top-level keys typically include
        ``results`` and ``meta``). Normalisation into the
        :class:`DiscoveredPaper` shape happens in the discovery service.

        Filters with ``None`` / empty values are omitted from the URL.
        """
        filters = filters or ProviderSearchFilters()
        params = self._build_search_params(query=query, filters=filters, limit=limit)
        return await self._get_json("/works", params=params)

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
            "search": query,
            "per-page": str(max(1, min(limit, 200))),
        }

        # Polite-pool: mailto contact when configured.
        email = self._settings.openalex_user_email
        if email and not _is_placeholder(email):
            params["mailto"] = email

        # Authenticated tier (premium key).
        api_key = self._settings.openalex_api_key
        if api_key and not _is_placeholder(api_key):
            params["api_key"] = api_key

        # OpenAlex composes its filter clause via a comma-joined
        # ``filter=key:val,...`` query string.
        filter_clauses: list[str] = []
        if filters.only_with_pdf:
            # has_oa_accepted_or_published_version is the OpenAlex filter
            # closest to "has a PDF we can probably fetch". This filter is
            # OFF by default.
            filter_clauses.append("has_oa_accepted_or_published_version:true")
        if filters.open_access_only:
            filter_clauses.append("is_oa:true")
        if filters.year_from is not None:
            filter_clauses.append(f"from_publication_date:{filters.year_from}-01-01")
        if filters.year_to is not None:
            filter_clauses.append(f"to_publication_date:{filters.year_to}-12-31")
        for work_type in filters.work_types:
            filter_clauses.append(f"type:{work_type}")
        if filter_clauses:
            params["filter"] = ",".join(filter_clauses)
        return params

    async def _get_json(self, path: str, *, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            if self._client is not None:
                resp = await self._client.get(url, params=params, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as c:
                    resp = await c.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"OpenAlex request timed out: {exc}",
                provider=self.PROVIDER_NAME,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"OpenAlex network error: {exc.__class__.__name__}",
                provider=self.PROVIDER_NAME,
            ) from exc

        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            raise ProviderRateLimited(
                f"OpenAlex rate limited (retry_after={retry_after}s)",
                provider=self.PROVIDER_NAME,
                retry_after=retry_after,
            )
        if resp.status_code == 404:
            raise ProviderNotFound(
                f"OpenAlex {path} not found",
                provider=self.PROVIDER_NAME,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"OpenAlex HTTP {resp.status_code}",
                provider=self.PROVIDER_NAME,
                status_code=resp.status_code,
            )
        return resp.json()


__all__ = [
    "DEFAULT_TIMEOUT",
    "OPENALEX_BASE_URL",
    "OpenAlexClient",
    "ProviderSearchFilters",
]
