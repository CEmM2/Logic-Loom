"""Tests for discovery cache invalidation + observability.

Covers:
  1. Forcing a new search after cache clear hits the providers again.

Plus pinned behaviours:
  - Cache key includes the request body hash AND provider API version
    (version bump invalidates entries).
  - GET /api/assistant/discover/cache/stats returns hit/miss counters
    and oldest entry timestamp.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.assistant.discovery import DiscoveredPaper, PaperDiscoveryService
from logic_loom_api.assistant.zotero_match import ZoteroCatalog, ZoteroMatchService
from logic_loom_api.config import Settings
from logic_loom_api.deps import get_settings
from logic_loom_api.main import api as fastapi_app
from logic_loom_api.routes import assistant_discover as discover_routes
from logic_loom_api.routes.assistant_discover import (
    _CACHE_STATS,
    _cache_key,
    get_paper_discovery_service,
    get_zotero_match_service,
)


class _StubDiscovery(PaperDiscoveryService):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.search_calls = 0

    async def search_with_status(self, query, *, filters=None, limit=25, providers=()):  # type: ignore[override]
        return await self.search(query, filters=filters, limit=limit), []

    async def search(self, query, *, filters=None, limit=25):  # type: ignore[override]
        self.search_calls += 1
        return [
            DiscoveredPaper(
                id="openalex:W1",
                source="merged",
                doi=f"10.x/{query}",
                openalex_id="W1",
                title=f"Result for {query}",
                year=2024,
            )
        ]


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        akms_workspace=tmp_path / "AKMS-Workspace",
        assistant_workspace=tmp_path / "Assistant-Workspace",
    )


@pytest.fixture()
def client(settings: Settings):
    discovery_stub = _StubDiscovery(settings)
    zotero_stub = ZoteroMatchService(settings, catalog=ZoteroCatalog())
    fastapi_app.dependency_overrides[get_settings] = lambda: settings
    fastapi_app.dependency_overrides[get_paper_discovery_service] = (
        lambda: discovery_stub
    )
    fastapi_app.dependency_overrides[get_zotero_match_service] = lambda: zotero_stub
    fastapi_app.state.discovery_stub = discovery_stub

    # Reset module-level counters so tests don't bleed.
    _CACHE_STATS["hits"] = 0
    _CACHE_STATS["misses"] = 0
    _CACHE_STATS["clears"] = 0

    with TestClient(fastapi_app) as c:
        yield c
    fastapi_app.dependency_overrides.clear()


class TestDiscoveryCacheInvalidation:
    """Tests for discovery cache invalidation + observability."""

    @pytest.mark.unit
    def test_cache_hit_miss_accounting_under_sequential_calls(
        self, client: TestClient
    ) -> None:
        """Verifies: /cache/stats reports incrementing miss-then-hit
        counters across two identical sequential calls."""
        stats_pre = client.get("/api/assistant/discover/cache/stats").json()
        assert stats_pre["hits"] == 0
        assert stats_pre["misses"] == 0

        client.post("/api/assistant/discover/search", json={"query": "graphene"})
        stats_after_first = client.get(
            "/api/assistant/discover/cache/stats"
        ).json()
        assert stats_after_first["misses"] == 1
        assert stats_after_first["hits"] == 0
        assert stats_after_first["entries"] == 1

        client.post("/api/assistant/discover/search", json={"query": "graphene"})
        stats_after_second = client.get(
            "/api/assistant/discover/cache/stats"
        ).json()
        assert stats_after_second["misses"] == 1
        assert stats_after_second["hits"] == 1
        # No new entry — the second call was served from cache.
        assert stats_after_second["entries"] == 1

    @pytest.mark.unit
    def test_provider_api_version_bump_invalidates_prior_entries(
        self, settings: Settings, client: TestClient, monkeypatch
    ) -> None:
        """Verifies: bumping DISCOVERY_PROVIDER_VERSION changes the cache
        key so a previously-cached request body misses on the next call.

        The disk file remains, but its key includes the old version
        string; with a new version the route computes a different key
        and writes a fresh entry.
        """
        body = {"query": "version-test"}
        # First call lands under version "v1".
        client.post("/api/assistant/discover/search", json=body)
        stub = fastapi_app.state.discovery_stub
        assert stub.search_calls == 1

        # Bump the version constant; the same body now maps to a new key.
        monkeypatch.setattr(discover_routes, "DISCOVERY_PROVIDER_VERSION", "v2")
        # Sanity-check the keying primitive.
        k_v1 = _cache_key(body=body, provider_version="v1")
        k_v2 = _cache_key(body=body, provider_version="v2")
        assert k_v1 != k_v2

        # Second call with the new version misses cache and re-runs.
        client.post("/api/assistant/discover/search", json=body)
        assert stub.search_calls == 2

    @pytest.mark.integration
    def test_delete_endpoint_clears_cache_and_stats_return_zero(
        self, client: TestClient
    ) -> None:
        """Verifies: DELETE /cache removes all entries; /cache/stats
        afterwards reports zero hits/misses/entries; a follow-up /search
        hits the providers again."""
        # Seed an entry.
        client.post("/api/assistant/discover/search", json={"query": "alpha"})
        stats_pre = client.get("/api/assistant/discover/cache/stats").json()
        assert stats_pre["entries"] == 1
        assert stats_pre["misses"] == 1
        stub = fastapi_app.state.discovery_stub
        assert stub.search_calls == 1

        # Clear.
        del_resp = client.delete("/api/assistant/discover/cache")
        assert del_resp.status_code == 200
        assert del_resp.json()["removed"] == 1

        # Stats reset.
        stats_post = client.get("/api/assistant/discover/cache/stats").json()
        assert stats_post["hits"] == 0
        assert stats_post["misses"] == 0
        assert stats_post["entries"] == 0
        assert stats_post["oldest_entry_epoch"] is None

        # Follow-up search re-hits the provider.
        client.post("/api/assistant/discover/search", json={"query": "alpha"})
        assert stub.search_calls == 2

    @pytest.mark.unit
    def test_cache_stats_reports_oldest_entry_timestamp(
        self, client: TestClient
    ) -> None:
        """Verifies: /cache/stats returns the oldest cached-entry epoch.
        Lets the UI render a "last refreshed" label."""
        # Empty cache reports null.
        empty = client.get("/api/assistant/discover/cache/stats").json()
        assert empty["oldest_entry_epoch"] is None

        # Seed an entry; the oldest_entry_epoch becomes a finite float.
        client.post("/api/assistant/discover/search", json={"query": "ts"})
        seeded = client.get("/api/assistant/discover/cache/stats").json()
        assert isinstance(seeded["oldest_entry_epoch"], (int, float))
        assert seeded["oldest_entry_epoch"] > 0
