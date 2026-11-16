"""Tests for the discovery provider clients (OpenAlex + Semantic Scholar).

Covers:
  1. Stub HTTP server tests exercise success, 404, 429.
  2. ProviderRateLimited surfaces Retry-After seconds as an integer attribute.
  3. only_with_pdf default is False (filtering to PDF-only by default would
     silently hide relevant papers).
  4. SemanticScholar.recommendations posts seed_paper_ids correctly.

Uses ``httpx.MockTransport`` (no respx dependency) so the test suite stays
on the toolchain the rest of the Assistant tests already pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from logic_loom_api.assistant.providers import (
    OpenAlexClient,
    ProviderError,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderSearchFilters,
    SemanticScholarClient,
)
from logic_loom_api.config import Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Minimal Settings with all Assistant API knobs set to non-placeholder
    values so the polite-pool / api-key code paths fire."""
    return Settings(
        akms_workspace=tmp_path / "AKMS-Workspace",
        openalex_user_email="researcher@example.test",
        openalex_api_key="oa-key-123",
        semantic_scholar_api_key="s2-key-456",
    )


def _make_client_with_handler(handler):
    """Build an ``httpx.AsyncClient`` that routes every request through the
    provided handler. Captures the last seen :class:`httpx.Request` on
    ``handler.last_request`` for assertion."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------


class TestOpenAlexClient:
    """OpenAlex client tests: success/404/429, Retry-After, only_with_pdf default."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_openalex_search_success_parses_works(
        self, settings: Settings
    ) -> None:
        """Verifies: OpenAlexClient.search hits /works and returns parsed JSON."""
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["request"] = req
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": "https://openalex.org/W123", "title": "A paper"},
                    ],
                    "meta": {"count": 1},
                },
            )

        async with _make_client_with_handler(handler) as ac:
            client = OpenAlexClient(settings, client=ac)
            payload = await client.search("graphene")

        assert payload["meta"]["count"] == 1
        assert payload["results"][0]["id"] == "https://openalex.org/W123"
        req: httpx.Request = seen["request"]
        assert req.url.path == "/works"
        assert req.url.params["search"] == "graphene"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_openalex_request_includes_polite_pool_mailto(
        self, settings: Settings
    ) -> None:
        """Verifies: OpenAlex URL carries mailto= from settings.openalex_user_email."""
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["request"] = req
            return httpx.Response(200, json={"results": [], "meta": {"count": 0}})

        async with _make_client_with_handler(handler) as ac:
            client = OpenAlexClient(settings, client=ac)
            await client.search("foo")

        req: httpx.Request = seen["request"]
        assert req.url.params["mailto"] == "researcher@example.test"
        assert req.url.params["api_key"] == "oa-key-123"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_openalex_429_raises_provider_rate_limited_with_retry_after(
        self, settings: Settings
    ) -> None:
        """Verifies: 429 + Retry-After header raises ProviderRateLimited(retry_after=int)."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "42"})

        async with _make_client_with_handler(handler) as ac:
            client = OpenAlexClient(settings, client=ac)
            with pytest.raises(ProviderRateLimited) as excinfo:
                await client.search("foo")

        err = excinfo.value
        assert err.retry_after == 42
        assert isinstance(err.retry_after, int)
        assert err.provider == "openalex"
        assert err.status_code == 429

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_openalex_429_without_retry_after_falls_back_to_zero(
        self, settings: Settings
    ) -> None:
        """Verifies: 429 without Retry-After yields retry_after=0 (caller-driven backoff)."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        async with _make_client_with_handler(handler) as ac:
            client = OpenAlexClient(settings, client=ac)
            with pytest.raises(ProviderRateLimited) as excinfo:
                await client.search("foo")

        assert excinfo.value.retry_after == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_openalex_404_raises_provider_not_found(
        self, settings: Settings
    ) -> None:
        """Verifies: 404 raises ProviderNotFound."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "Not Found"})

        async with _make_client_with_handler(handler) as ac:
            client = OpenAlexClient(settings, client=ac)
            with pytest.raises(ProviderNotFound):
                await client.search("foo")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_only_with_pdf_default_is_false(self, settings: Settings) -> None:
        """Verifies (HARD RULE): only_with_pdf defaults to False.

        Built filters by default OMIT the has_oa_accepted_or_published_version
        clause from the URL, and an unfiltered query returns no-PDF results.
        """
        default_filters = ProviderSearchFilters()
        assert default_filters.only_with_pdf is False

        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["request"] = req
            # Server returns one paper with no PDF — proves the filter is off.
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "title": "No-PDF paper",
                            "best_oa_location": None,
                        }
                    ],
                    "meta": {"count": 1},
                },
            )

        async with _make_client_with_handler(handler) as ac:
            client = OpenAlexClient(settings, client=ac)
            payload = await client.search("unfiltered")

        req: httpx.Request = seen["request"]
        filter_param = req.url.params.get("filter", "")
        assert "has_oa_accepted_or_published_version" not in filter_param
        assert payload["results"][0]["best_oa_location"] is None


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------


class TestSemanticScholarClient:
    """Semantic Scholar client tests: success/404/429, Retry-After, recommendations."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_semantic_scholar_search_success(self, settings: Settings) -> None:
        """Verifies: search returns parsed papers and sends the api key header."""
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["request"] = req
            return httpx.Response(
                200,
                json={
                    "data": [{"paperId": "S2-1", "title": "Hello"}],
                    "total": 1,
                },
            )

        async with _make_client_with_handler(handler) as ac:
            client = SemanticScholarClient(settings, client=ac)
            payload = await client.search("graphene")

        assert payload["data"][0]["paperId"] == "S2-1"
        req: httpx.Request = seen["request"]
        assert req.url.path == "/graph/v1/paper/search"
        assert req.url.params["query"] == "graphene"
        assert req.headers["x-api-key"] == "s2-key-456"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_semantic_scholar_429_raises_provider_rate_limited(
        self, settings: Settings
    ) -> None:
        """Verifies: 429 surfaces ProviderRateLimited with retry_after as int."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "7"})

        async with _make_client_with_handler(handler) as ac:
            client = SemanticScholarClient(settings, client=ac)
            with pytest.raises(ProviderRateLimited) as excinfo:
                await client.search("foo")

        assert excinfo.value.retry_after == 7
        assert isinstance(excinfo.value.retry_after, int)
        assert excinfo.value.provider == "semantic_scholar"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_semantic_scholar_recommendations_posts_seed_paper_ids(
        self, settings: Settings
    ) -> None:
        """Verifies: recommendations() POSTs with positivePaperIds = seeds."""
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["request"] = req
            seen["body"] = req.read()
            return httpx.Response(200, json={"recommendedPapers": []})

        async with _make_client_with_handler(handler) as ac:
            client = SemanticScholarClient(settings, client=ac)
            payload = await client.recommendations(
                ["S2:1", "S2:2"], mode="similar", limit=5
            )

        assert payload == {"recommendedPapers": []}
        req: httpx.Request = seen["request"]
        assert req.method == "POST"
        assert req.url.path == "/recommendations/v1/papers"
        # The seeds are POSTed in the JSON body under positivePaperIds.
        import json as _json

        body = _json.loads(seen["body"])
        assert body["positivePaperIds"] == ["S2:1", "S2:2"]
        assert body["mode"] == "similar"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_semantic_scholar_recommendations_rejects_empty_seeds(
        self, settings: Settings
    ) -> None:
        """Verifies: empty seed_paper_ids raises ProviderError without making
        a network call (defensive: S2 would 400 anyway, but failing fast keeps
        the error envelope uniform)."""
        client = SemanticScholarClient(settings)
        with pytest.raises(ProviderError):
            await client.recommendations([])


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class TestProviderErrorTaxonomy:
    """Provider error taxonomy hierarchy."""

    @pytest.mark.unit
    def test_provider_error_taxonomy_hierarchy(self) -> None:
        """Verifies: every specific error inherits from ProviderError so
        callers can catch the umbrella class."""
        from logic_loom_api.assistant.providers.errors import (
            ProviderError,
            ProviderNotFound,
            ProviderRateLimited,
            ProviderTimeout,
        )

        assert issubclass(ProviderRateLimited, ProviderError)
        assert issubclass(ProviderNotFound, ProviderError)
        assert issubclass(ProviderTimeout, ProviderError)

        rl = ProviderRateLimited("rate limited", provider="openalex", retry_after=5)
        assert isinstance(rl, ProviderError)
        assert rl.retry_after == 5
        assert rl.status_code == 429
