"""Tests for the related-discovery routes.

Covers: POST /api/assistant/discover/related returns ranked,
deduplicated, crossref-tagged results.

Mounted onto the existing assistant_discover router; reuses the Zotero
crossref pipeline + the related-cache plumbing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.assistant.discovery import DiscoveredPaper
from logic_loom_api.assistant.related import RelatedPaperService
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
from logic_loom_api.zsum_pipeline.index_reader import ItemIndex


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubRelated(RelatedPaperService):
    """Records which mode method got called and returns canned papers."""

    def __init__(
        self,
        settings: Settings,
        *,
        papers_by_mode: dict[str, list[DiscoveredPaper]] | None = None,
    ) -> None:
        super().__init__(settings)
        self._papers_by_mode = papers_by_mode or {}
        self.calls: list[dict[str, Any]] = []

    async def find_similar(self, seeds, *, limit=25, ranking="most_relevant"):
        self.calls.append({"mode": "similar", "seeds": list(seeds), "limit": limit, "ranking": ranking})
        return list(self._papers_by_mode.get("similar", []))

    async def find_foundational(self, seeds, *, limit=25, ranking="most_cited"):
        self.calls.append({"mode": "foundational", "seeds": list(seeds), "limit": limit, "ranking": ranking})
        return list(self._papers_by_mode.get("foundational", []))

    async def find_recent_citing(
        self,
        seeds,
        *,
        year_from=None,
        year_to=None,
        limit=25,
        ranking="recent_and_relevant",
    ):
        self.calls.append(
            {
                "mode": "recent_citing",
                "seeds": list(seeds),
                "year_from": year_from,
                "year_to": year_to,
                "limit": limit,
                "ranking": ranking,
            }
        )
        return list(self._papers_by_mode.get("recent_citing", []))

    async def find_highly_cited_neighbours(self, seeds, *, limit=25, ranking="most_cited"):
        self.calls.append(
            {"mode": "highly_cited_neighbours", "seeds": list(seeds), "limit": limit, "ranking": ranking}
        )
        return list(self._papers_by_mode.get("highly_cited_neighbours", []))

    async def find_review_candidates(self, seeds, *, limit=25, ranking="most_cited"):
        self.calls.append(
            {"mode": "review_candidates", "seeds": list(seeds), "limit": limit, "ranking": ranking}
        )
        return list(self._papers_by_mode.get("review_candidates", []))


def _make_paper(
    pid: str = "openalex:W1",
    *,
    doi: str | None = "10.1/x",
    title: str = "Sample",
    pdf_url: str | None = "https://example.org/p.pdf",
    is_open_access: bool | None = True,
    year: int = 2022,
) -> DiscoveredPaper:
    return DiscoveredPaper(
        id=pid,
        source="openalex",
        doi=doi,
        openalex_id=pid.split(":", 1)[1] if pid.startswith("openalex:") else None,
        semantic_scholar_id=pid.split(":", 1)[1] if pid.startswith("semantic_scholar:") else None,
        title=title,
        year=year,
        pdf_url=pdf_url,
        is_open_access=is_open_access,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        openalex_user_email="dev@example.test",
    )


@pytest.fixture()
def catalog() -> ZoteroCatalog:
    """Zotero catalog with one known DOI -> citekey mapping."""
    return ZoteroCatalog(
        by_doi={"10.1/match": "in_lib2022"},
        items_by_citekey={
            "in_lib2022": ItemIndex(
                citekey="in_lib2022",
                year=2022,
                venue="J",
                collections=[],
                card_state="cached",
            )
        },
    )


@pytest.fixture()
def client_factory(settings, catalog):
    """Returns a callable that builds a TestClient with the stub related
    service preloaded with the given papers-per-mode mapping."""

    created_clients: list[TestClient] = []

    def _factory(papers_by_mode: dict[str, list[DiscoveredPaper]]):
        related_stub = _StubRelated(settings, papers_by_mode=papers_by_mode)
        zotero_stub = ZoteroMatchService(settings, catalog=catalog)
        fastapi_app.dependency_overrides[get_settings] = lambda: settings
        fastapi_app.dependency_overrides[get_related_paper_service] = (
            lambda: related_stub
        )
        fastapi_app.dependency_overrides[get_zotero_match_service] = (
            lambda: zotero_stub
        )
        fastapi_app.state.related_stub = related_stub
        c = TestClient(fastapi_app)
        created_clients.append(c)
        return c

    yield _factory

    for c in created_clients:
        c.close()
    fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRelatedDiscoveryRoutes:
    """Integration tests for POST /api/assistant/discover/related."""

    @pytest.mark.integration
    def test_related_route_returns_ranked_and_crossreffed_results(
        self, client_factory
    ) -> None:
        """Verifies: /api/assistant/discover/related runs the right
        mode method, decorates with ZoteroMatch, and returns ranked
        results."""
        papers = [
            _make_paper("openalex:W1", doi="10.1/match", title="In-library"),
            _make_paper("openalex:W2", doi="10.1/elsewhere", title="Not-in-library"),
        ]
        c = client_factory({"similar": papers})
        resp = c.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:W_SEED"],
                "mode": "similar",
                "ranking": "most_relevant",
                "limit": 10,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "similar"
        assert body["ranking"] == "most_relevant"
        assert body["cached"] is False
        assert len(body["results"]) == 2
        # Crossref tag attached.
        first = body["results"][0]
        assert first["zotero_match"]["citekey"] == "in_lib2022"
        assert first["zotero_match"]["in_zotero"] is True

    @pytest.mark.integration
    def test_related_route_supports_all_five_modes(self, client_factory) -> None:
        """Verifies: each of the five mode values dispatches to the right
        RelatedPaperService method."""
        for mode in (
            "similar",
            "foundational",
            "recent_citing",
            "highly_cited_neighbours",
            "review_candidates",
        ):
            c = client_factory({mode: [_make_paper(title=f"r-{mode}")]})
            resp = c.post(
                "/api/assistant/discover/related",
                json={
                    "seed_paper_ids": ["openalex:W_SEED"],
                    "mode": mode,
                    "ranking": "most_relevant",
                },
            )
            assert resp.status_code == 200, f"{mode}: {resp.text}"
            assert resp.json()["mode"] == mode
            calls = fastapi_app.state.related_stub.calls
            assert calls[-1]["mode"] == mode

    @pytest.mark.integration
    def test_related_route_zotero_crossref_attaches_to_each_paper(
        self, client_factory
    ) -> None:
        """Verifies: every returned paper carries a ZoteroMatch dict."""
        c = client_factory(
            {"foundational": [_make_paper("openalex:WA"), _make_paper("openalex:WB", doi="10.1/match")]}
        )
        resp = c.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:W_SEED"],
                "mode": "foundational",
                "ranking": "most_cited",
            },
        )
        rows = resp.json()["results"]
        assert all("zotero_match" in r for r in rows)
        assert all(
            {"citekey", "confidence", "match_reason", "in_zotero"}
            <= set(r["zotero_match"].keys())
            for r in rows
        )

    @pytest.mark.integration
    def test_related_route_filters_only_with_pdf_default_is_false(
        self, client_factory
    ) -> None:
        """Verifies (HARD RULE): omitting only_with_pdf yields False;
        papers without a pdf_url surface in the response."""
        c = client_factory({"similar": [_make_paper(pdf_url=None)]})
        resp = c.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:W_SEED"],
                "mode": "similar",
                "ranking": "most_relevant",
            },
        )
        assert resp.status_code == 200
        # The paper survived even with pdf_url=None.
        assert len(resp.json()["results"]) == 1
        # Pin the Pydantic default just like the search route test does.
        from logic_loom_api.routes.assistant_discover import (
            DiscoverRelatedFiltersBody,
        )

        assert DiscoverRelatedFiltersBody().only_with_pdf is False

    @pytest.mark.integration
    def test_related_route_filters_only_with_pdf_true_drops_no_pdf_papers(
        self, client_factory
    ) -> None:
        """Verifies: when only_with_pdf=True is explicit, papers with
        pdf_url=None are dropped."""
        c = client_factory(
            {
                "similar": [
                    _make_paper("openalex:WA", pdf_url=None, title="No PDF"),
                    _make_paper("openalex:WB", pdf_url="https://x/p.pdf", title="Has PDF"),
                ]
            }
        )
        resp = c.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:W_SEED"],
                "mode": "similar",
                "ranking": "most_relevant",
                "filters": {"only_with_pdf": True},
            },
        )
        rows = resp.json()["results"]
        assert len(rows) == 1
        assert rows[0]["title"] == "Has PDF"

    @pytest.mark.integration
    def test_related_route_hide_in_zotero_filter(self, client_factory) -> None:
        """Verifies: hide_in_zotero filter drops papers whose Zotero
        match has in_zotero=True."""
        c = client_factory(
            {
                "similar": [
                    _make_paper("openalex:WA", doi="10.1/match", title="In Zotero"),
                    _make_paper("openalex:WB", doi="10.1/elsewhere", title="Not in Zotero"),
                ]
            }
        )
        resp = c.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:W_SEED"],
                "mode": "similar",
                "ranking": "most_relevant",
                "filters": {"hide_in_zotero": True},
            },
        )
        rows = resp.json()["results"]
        assert len(rows) == 1
        assert rows[0]["title"] == "Not in Zotero"

    @pytest.mark.integration
    def test_related_route_rejects_unknown_mode(self, client_factory) -> None:
        """Verifies: unknown mode -> 400 with the documented error code.

        The standard envelope handler (`logic_loom_api/errors.py`) writes
        ``error.code`` from the HTTPException's ``code`` attribute set
        via :func:`http_error`.
        """
        c = client_factory({})
        resp = c.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:W_SEED"],
                "mode": "bogus",
                "ranking": "most_relevant",
            },
        )
        assert resp.status_code == 400
        env = resp.json()["error"]
        assert env["code"] == "unknown_related_mode"
        # The HTTPException message stringifies in the envelope so the
        # mode name is visible to humans / logs.
        assert "bogus" in env["message"]

    @pytest.mark.integration
    def test_related_route_rejects_unknown_ranking(self, client_factory) -> None:
        """Verifies: unknown ranking -> 400 with the documented error code."""
        c = client_factory({})
        resp = c.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:W_SEED"],
                "mode": "similar",
                "ranking": "magic",
            },
        )
        assert resp.status_code == 400
        env = resp.json()["error"]
        assert env["code"] == "unknown_ranking_preset"
        assert "magic" in env["message"]

    @pytest.mark.integration
    def test_related_route_recent_citing_passes_year_range(
        self, client_factory
    ) -> None:
        """Verifies: recent_citing mode forwards year_from / year_to to
        the service method (not just the http body)."""
        c = client_factory({"recent_citing": [_make_paper()]})
        resp = c.post(
            "/api/assistant/discover/related",
            json={
                "seed_paper_ids": ["openalex:W_SEED"],
                "mode": "recent_citing",
                "ranking": "recent_and_relevant",
                "year_from": 2020,
                "year_to": 2024,
            },
        )
        assert resp.status_code == 200
        call = fastapi_app.state.related_stub.calls[-1]
        assert call["mode"] == "recent_citing"
        assert call["year_from"] == 2020
        assert call["year_to"] == 2024
