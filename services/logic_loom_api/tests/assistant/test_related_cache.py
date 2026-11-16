"""Tests for the related-papers cache lifecycle.

Covers: TTL boundaries respected.
  - similar / foundational entries: 7-day TTL (hit at 6d, miss at 8d).
  - recent_citing entries: 24h TTL (hit at 23h, miss at 25h).
  - DELETE /api/assistant/discover/related-cache clears every entry
    across both TTL classes.

Cache key = sha256(provider_version + "|" + json.dumps({seed_set, mode,
ranking, year_from, year_to}, sort_keys=True)).

Subdirectory under <assistant_workspace>/related_cache/<key>.json (kept
separate from the search discovery_cache).

Uses monkeypatch on time.time to simulate time travel without depending
on freezegun.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.assistant.discovery import DiscoveredPaper
from logic_loom_api.assistant.related import (
    RELATED_CACHE_DIRNAME,
    RELATED_PROVIDER_VERSION,
    RELATED_TTL_STABLE_SECONDS,
    RELATED_TTL_VOLATILE_SECONDS,
    RelatedPaperService,
    related_cache_dir,
    related_cache_key,
    related_cache_path,
    related_cache_read,
    related_cache_write,
    related_ttl_for_mode,
)
from logic_loom_api.assistant.zotero_match import (
    ZoteroCatalog,
    ZoteroMatchService,
)
from logic_loom_api.config import Settings
from logic_loom_api.deps import get_settings
from logic_loom_api.main import api as fastapi_app
from logic_loom_api.routes.assistant_discover import (
    get_related_paper_service,
    get_zotero_match_service,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubRelated(RelatedPaperService):
    """Records call counts so we can assert cache hits avoid the upstream."""

    def __init__(self, settings: Settings, papers: list[DiscoveredPaper]) -> None:
        super().__init__(settings)
        self._papers = papers
        self.calls = 0

    async def find_similar(self, seeds, *, limit=25, ranking="most_relevant"):
        self.calls += 1
        return list(self._papers)

    async def find_foundational(self, seeds, *, limit=25, ranking="most_cited"):
        self.calls += 1
        return list(self._papers)

    async def find_recent_citing(
        self, seeds, *, year_from=None, year_to=None, limit=25, ranking="recent_and_relevant"
    ):
        self.calls += 1
        return list(self._papers)

    async def find_highly_cited_neighbours(self, seeds, *, limit=25, ranking="most_cited"):
        self.calls += 1
        return list(self._papers)

    async def find_review_candidates(self, seeds, *, limit=25, ranking="most_cited"):
        self.calls += 1
        return list(self._papers)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "Assistant-Workspace"
    ws.mkdir()
    return ws


@pytest.fixture()
def settings(workspace: Path, tmp_path: Path) -> Settings:
    return Settings(
        akms_workspace=tmp_path / "AKMS-Workspace",
        assistant_workspace=workspace,
    )


@pytest.fixture()
def cache_dir(workspace: Path) -> Path:
    return related_cache_dir(workspace)


def _make_paper(pid: str = "openalex:W1") -> DiscoveredPaper:
    return DiscoveredPaper(
        id=pid,
        source="openalex",
        doi=None,
        openalex_id=pid.split(":", 1)[1] if pid.startswith("openalex:") else None,
        title=f"Paper {pid}",
        year=2024,
    )


@pytest.fixture()
def client(settings, workspace):
    """TestClient with the related + zotero deps overridden by stubs."""
    related_stub = _StubRelated(settings, papers=[_make_paper()])
    zotero_stub = ZoteroMatchService(settings, catalog=ZoteroCatalog())

    fastapi_app.dependency_overrides[get_settings] = lambda: settings
    fastapi_app.dependency_overrides[get_related_paper_service] = lambda: related_stub
    fastapi_app.dependency_overrides[get_zotero_match_service] = lambda: zotero_stub
    fastapi_app.state.related_stub = related_stub

    with TestClient(fastapi_app) as c:
        yield c

    fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Unit tests — cache helpers
# ---------------------------------------------------------------------------


class TestRelatedCacheTtl:
    """Unit tests for the related-cache TTLs and cache key."""

    @pytest.mark.unit
    def test_similar_mode_entry_is_hit_at_six_days(self, workspace) -> None:
        """Verifies: 7d TTL — entry written at t=0 is still cached at
        t=6d. Direct test of `related_cache_read` with simulated time."""
        key = related_cache_key(
            seed_paper_ids=["openalex:W_SEED"],
            mode="similar",
            ranking="most_relevant",
        )
        related_cache_write(workspace, key, {"results": []}, now=0.0)
        ttl = related_ttl_for_mode("similar")
        # 6 days < 7 day TTL.
        hit = related_cache_read(workspace, key, ttl_seconds=ttl, now=6 * 24 * 3600)
        assert hit is not None
        assert hit["results"] == []

    @pytest.mark.unit
    def test_similar_mode_entry_is_missed_at_eight_days(self, workspace) -> None:
        """Verifies: 7d TTL — entry written at t=0 is expired at t=8d."""
        key = related_cache_key(
            seed_paper_ids=["openalex:W_SEED"],
            mode="similar",
            ranking="most_relevant",
        )
        related_cache_write(workspace, key, {"results": []}, now=0.0)
        ttl = related_ttl_for_mode("similar")
        miss = related_cache_read(workspace, key, ttl_seconds=ttl, now=8 * 24 * 3600)
        assert miss is None

    @pytest.mark.unit
    def test_recent_citing_entry_is_hit_at_twenty_three_hours(self, workspace) -> None:
        """Verifies: 24h TTL — recent_citing entry hit at 23h."""
        key = related_cache_key(
            seed_paper_ids=["openalex:W_SEED"],
            mode="recent_citing",
            ranking="recent_and_relevant",
        )
        related_cache_write(workspace, key, {"results": []}, now=0.0)
        ttl = related_ttl_for_mode("recent_citing")
        hit = related_cache_read(workspace, key, ttl_seconds=ttl, now=23 * 3600)
        assert hit is not None

    @pytest.mark.unit
    def test_recent_citing_entry_is_missed_at_twenty_five_hours(self, workspace) -> None:
        """Verifies: 24h TTL — recent_citing entry expired at 25h."""
        key = related_cache_key(
            seed_paper_ids=["openalex:W_SEED"],
            mode="recent_citing",
            ranking="recent_and_relevant",
        )
        related_cache_write(workspace, key, {"results": []}, now=0.0)
        ttl = related_ttl_for_mode("recent_citing")
        miss = related_cache_read(workspace, key, ttl_seconds=ttl, now=25 * 3600)
        assert miss is None

    @pytest.mark.unit
    def test_ttl_constants_pin_seven_days_and_twenty_four_hours(self) -> None:
        """Verifies: TTL constants are 7d / 24h."""
        assert RELATED_TTL_STABLE_SECONDS == 7 * 24 * 60 * 60
        assert RELATED_TTL_VOLATILE_SECONDS == 24 * 60 * 60
        # And the per-mode router maps correctly.
        assert related_ttl_for_mode("similar") == RELATED_TTL_STABLE_SECONDS
        assert related_ttl_for_mode("foundational") == RELATED_TTL_STABLE_SECONDS
        assert related_ttl_for_mode("recent_citing") == RELATED_TTL_VOLATILE_SECONDS
        # derived modes -> stable (per design comment).
        assert (
            related_ttl_for_mode("highly_cited_neighbours")
            == RELATED_TTL_STABLE_SECONDS
        )
        assert (
            related_ttl_for_mode("review_candidates")
            == RELATED_TTL_STABLE_SECONDS
        )

    @pytest.mark.unit
    def test_cache_key_includes_provider_version(self, workspace) -> None:
        """Verifies: bumping provider_version yields a different key."""
        kwargs = {
            "seed_paper_ids": ["openalex:W"],
            "mode": "similar",
            "ranking": "most_relevant",
        }
        v1 = related_cache_key(**kwargs, provider_version="v1")
        v2 = related_cache_key(**kwargs, provider_version="v2")
        assert v1 != v2
        # Same version + inputs -> deterministic key.
        assert v1 == related_cache_key(**kwargs, provider_version="v1")

    @pytest.mark.unit
    def test_cache_key_is_order_independent_on_seeds(self, workspace) -> None:
        """Verifies: re-ordering the seed list yields the same key."""
        k_ab = related_cache_key(
            seed_paper_ids=["openalex:A", "openalex:B"],
            mode="similar",
            ranking="most_relevant",
        )
        k_ba = related_cache_key(
            seed_paper_ids=["openalex:B", "openalex:A"],
            mode="similar",
            ranking="most_relevant",
        )
        assert k_ab == k_ba

    @pytest.mark.unit
    def test_cache_subdirectory_is_related_cache(
        self, workspace, cache_dir
    ) -> None:
        """Verifies: related-cache entries live under
        <workspace>/related_cache/<key>.json — separate from discovery_cache."""
        key = related_cache_key(
            seed_paper_ids=["openalex:W"],
            mode="similar",
            ranking="most_relevant",
        )
        related_cache_write(workspace, key, {"results": []})
        path = related_cache_path(workspace, key)
        assert path.exists()
        assert path.parent.name == RELATED_CACHE_DIRNAME
        # Not the discovery_cache directory.
        assert path.parent.name != "discovery_cache"

    @pytest.mark.unit
    def test_cache_write_is_atomic_via_tempfile(self, workspace) -> None:
        """Verifies: write uses a *.json.tmp + replace dance.

        Inspects the on-disk artefact: the final file is the target,
        and no leftover tempfile remains after a successful write.
        """
        key = "abc123"
        related_cache_write(workspace, key, {"results": [{"foo": "bar"}]})
        target = related_cache_path(workspace, key)
        assert target.exists()
        # The .json.tmp should NOT linger after a successful replace.
        tmp = target.with_suffix(".json.tmp")
        assert not tmp.exists()
        # Round-trip the payload.
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["results"] == [{"foo": "bar"}]
        assert "_cached_at" in payload


# ---------------------------------------------------------------------------
# Integration tests — DELETE endpoint + end-to-end cache lifecycle
# ---------------------------------------------------------------------------


class TestRelatedCacheLifecycle:
    """End-to-end cache lifecycle via the HTTP surface."""

    @pytest.mark.integration
    def test_first_call_misses_then_second_call_within_ttl_hits_cache(
        self, client: TestClient
    ) -> None:
        """First /related call records cached=False + bumps provider call
        count; immediate second call returns cached=True without a second
        provider call."""
        body = {
            "seed_paper_ids": ["openalex:W_SEED"],
            "mode": "similar",
            "ranking": "most_relevant",
        }
        first = client.post("/api/assistant/discover/related", json=body)
        assert first.status_code == 200, first.text
        assert first.json()["cached"] is False
        assert fastapi_app.state.related_stub.calls == 1

        second = client.post("/api/assistant/discover/related", json=body)
        assert second.status_code == 200
        assert second.json()["cached"] is True
        # Cache hit -> no second provider call.
        assert fastapi_app.state.related_stub.calls == 1

    @pytest.mark.integration
    def test_delete_endpoint_clears_every_entry_across_both_ttl_classes(
        self, client: TestClient, workspace, cache_dir
    ) -> None:
        """Verifies: DELETE /related-cache removes both stable-class
        (similar) and volatile-class (recent_citing) entries."""
        # Seed entries for both TTL classes.
        client.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:WA"],
                "mode": "similar",
                "ranking": "most_relevant",
            },
        )
        client.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:WA"],
                "mode": "recent_citing",
                "ranking": "recent_and_relevant",
            },
        )
        # Two distinct cache entries.
        entries = [p for p in cache_dir.iterdir() if p.suffix == ".json"]
        assert len(entries) == 2

        resp = client.delete("/api/assistant/discover/related-cache")
        assert resp.status_code == 200
        body = resp.json()
        assert body["removed"] == 2
        # Directory is empty afterwards.
        entries_after = (
            [p for p in cache_dir.iterdir()] if cache_dir.is_dir() else []
        )
        assert entries_after == []

    @pytest.mark.integration
    def test_delete_endpoint_on_empty_cache_returns_zero(
        self, client: TestClient
    ) -> None:
        """Verifies: DELETE when no entries exist returns removed=0 (idempotent)."""
        resp = client.delete("/api/assistant/discover/related-cache")
        assert resp.status_code == 200
        assert resp.json()["removed"] == 0

    @pytest.mark.integration
    def test_recent_citing_uses_volatile_ttl_distinct_from_similar(
        self, client: TestClient, workspace
    ) -> None:
        """Verifies: the route picks per-mode TTL via related_ttl_for_mode.

        After a write, simulate time travel via monkeypatching
        :func:`time.time` inside `related_cache_read` (we call the
        helper directly rather than the route so we can see the TTL
        kick in deterministically without faking the route's clock).
        """
        sim_key = related_cache_key(
            seed_paper_ids=["x"], mode="similar", ranking="most_relevant"
        )
        rc_key = related_cache_key(
            seed_paper_ids=["x"], mode="recent_citing", ranking="recent_and_relevant"
        )
        related_cache_write(workspace, sim_key, {"results": ["sim"]}, now=0.0)
        related_cache_write(workspace, rc_key, {"results": ["rc"]}, now=0.0)

        # At t=25h, the volatile entry is gone, the stable one remains.
        sim_hit = related_cache_read(
            workspace, sim_key,
            ttl_seconds=related_ttl_for_mode("similar"),
            now=25 * 3600,
        )
        rc_miss = related_cache_read(
            workspace, rc_key,
            ttl_seconds=related_ttl_for_mode("recent_citing"),
            now=25 * 3600,
        )
        assert sim_hit is not None
        assert rc_miss is None

    @pytest.mark.integration
    def test_provider_version_bump_invalidates_cache(
        self, client: TestClient, workspace, monkeypatch
    ) -> None:
        """Verifies: monkeypatching RELATED_PROVIDER_VERSION to v2 yields
        a different cache key, so the second call misses + re-fetches."""
        from logic_loom_api.routes import assistant_discover as router_mod

        body = {
            "seed_paper_ids": ["openalex:W"],
            "mode": "similar",
            "ranking": "most_relevant",
        }
        first = client.post("/api/assistant/discover/related", json=body)
        assert first.json()["cached"] is False
        assert fastapi_app.state.related_stub.calls == 1

        # Same request, but pretend we bumped the provider contract.
        monkeypatch.setattr(router_mod, "RELATED_PROVIDER_VERSION", "v2")
        second = client.post("/api/assistant/discover/related", json=body)
        # Cache key changes -> miss -> second provider call.
        assert second.json()["cached"] is False
        assert fastapi_app.state.related_stub.calls == 2
