"""Tests for the RelatedPaperService core.

Covers:
  1. Each mode returns a deduplicated list of DiscoveredPaper.
  2. Ranking presets (highest-cited, most-recent) behave as documented.

The five modes:
  - find_similar(seeds, ...) -> S2 recommendations + OpenAlex related_to
  - find_foundational(seeds, ...) -> OpenAlex/S2 references graph walk
  - find_recent_citing(seeds, year_range, ...) -> cited-by graph
  - find_highly_cited_neighbours(seeds, ...) -> union(related + cited +
    citing), ranked by log(citation_count+1)
  - find_review_candidates(seeds, ...) -> title/abstract token filter
    {review, survey, overview, meta-analysis} weighted by reference count
"""
from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from logic_loom_api.assistant.discovery import DiscoveredPaper
from logic_loom_api.assistant.related import (
    RANKING_PRESETS,
    RELATED_MODES,
    REVIEW_TOKENS,
    RelatedPaperService,
    is_review_candidate,
    rank_papers,
)
from logic_loom_api.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(tmp_path) -> Settings:
    return Settings(
        akms_workspace=tmp_path / "AKMS-Workspace",
        assistant_workspace=tmp_path / "Assistant-Workspace",
        openalex_user_email="dev@example.test",
    )


def _make_oa_work(
    work_id: str,
    *,
    title: str,
    doi: str | None = None,
    year: int = 2020,
    citations: int = 0,
    abstract: str | None = None,
    referenced_works: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "title": title,
        "publication_year": year,
        "cited_by_count": citations,
        "abstract_inverted_index": (
            {w: [i] for i, w in enumerate(abstract.split())} if abstract else None
        ),
        "referenced_works": [
            f"https://openalex.org/{r}" for r in (referenced_works or [])
        ],
        "authorships": [],
    }


def _make_s2_paper(
    paper_id: str,
    *,
    title: str,
    doi: str | None = None,
    year: int = 2020,
    citations: int = 0,
    reference_count: int = 0,
    abstract: str | None = None,
) -> dict[str, Any]:
    return {
        "paperId": paper_id,
        "title": title,
        "abstract": abstract,
        "year": year,
        "authors": [],
        "externalIds": {"DOI": doi} if doi else {},
        "citationCount": citations,
        "referenceCount": reference_count,
    }


def _make_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_make_transport(handler))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRelatedPaperService:
    """Unit tests for the related-paper modes and ranking presets."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_find_similar_returns_deduplicated_discovered_papers(
        self, tmp_path
    ) -> None:
        """Verifies: find_similar() returns a dedup'd list.

        The S2 recommendations + OpenAlex related_to lookups both surface
        the same DOI; merge_results collapses them to one row.
        """
        shared_doi = "10.1/shared"

        async def s2_handler(request: httpx.Request) -> httpx.Response:
            assert "/recommendations/v1/papers" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "recommendedPapers": [
                        _make_s2_paper("S1", title="Shared", doi=shared_doi, year=2022)
                    ]
                },
            )

        async def oa_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            assert "/works" in url
            # httpx URL-encodes the ':' inside the filter clause to %3A.
            assert ("related_to:" in url) or ("related_to%3A" in url)
            return httpx.Response(
                200,
                json={
                    "results": [
                        _make_oa_work("W1", title="Shared", doi=shared_doi, year=2022)
                    ]
                },
            )

        svc = RelatedPaperService(
            _settings(tmp_path),
            openalex_client=_client(oa_handler),
            semantic_scholar_client=_client(s2_handler),
        )
        results = await svc.find_similar(
            ["semantic_scholar:SEED1", "openalex:W_SEED"], limit=10
        )

        # Single merged row (DOI overlap).
        assert len(results) == 1
        row = results[0]
        assert row.source == "merged"
        assert row.doi == shared_doi
        assert row.openalex_id == "W1"
        assert row.semantic_scholar_id == "S1"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_find_foundational_returns_dedup_and_sorted_by_citation_count(
        self, tmp_path
    ) -> None:
        """Verifies: find_foundational() walks references and
        sorts by citation count desc (default ranking=most_cited).
        """
        seed = "W_SEED"
        # The seed walk returns referenced_works -> we then bulk-fetch those.
        oa_calls: list[str] = []

        async def oa_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            oa_calls.append(url)
            if f"/works/{seed}" in url:
                return httpx.Response(
                    200,
                    json=_make_oa_work(
                        seed,
                        title="Seed",
                        year=2023,
                        referenced_works=["W_REF1", "W_REF2"],
                    ),
                )
            # Batched /works lookup for the references.
            assert ("ids.openalex:" in url) or ("ids.openalex%3A" in url)
            return httpx.Response(
                200,
                json={
                    "results": [
                        _make_oa_work("W_REF1", title="Cited Less", citations=10, year=2010),
                        _make_oa_work("W_REF2", title="Cited Most", citations=999, year=2008),
                    ]
                },
            )

        async def s2_handler(request: httpx.Request) -> httpx.Response:
            # Empty S2 side — no S2 seeds.
            return httpx.Response(200, json={"data": []})

        svc = RelatedPaperService(
            _settings(tmp_path),
            openalex_client=_client(oa_handler),
            semantic_scholar_client=_client(s2_handler),
        )
        results = await svc.find_foundational([f"openalex:{seed}"], limit=10)

        # Two papers, sorted by citations desc.
        assert len(results) == 2
        assert results[0].title == "Cited Most"
        assert results[1].title == "Cited Less"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_find_recent_citing_filters_by_year_range_and_dedupes(
        self, tmp_path
    ) -> None:
        """Verifies: find_recent_citing(year_from, year_to) keeps only
        papers within the year window and dedups across seeds.
        """

        async def oa_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            assert "/works" in url
            assert ("cites:" in url) or ("cites%3A" in url)
            # OpenAlex applies year filter server-side via from/to.
            return httpx.Response(
                200,
                json={
                    "results": [
                        _make_oa_work("W_CIT_NEW", title="New citer", year=2024, citations=5),
                    ]
                },
            )

        async def s2_handler(request: httpx.Request) -> httpx.Response:
            # S2 returns two citing papers; only the in-range one survives the
            # client-side year filter.
            assert "/citations" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "citingPaper": _make_s2_paper(
                                "S_NEW", title="Modern", year=2024
                            )
                        },
                        {
                            "citingPaper": _make_s2_paper(
                                "S_OLD", title="Ancient", year=2001
                            )
                        },
                    ]
                },
            )

        svc = RelatedPaperService(
            _settings(tmp_path),
            openalex_client=_client(oa_handler),
            semantic_scholar_client=_client(s2_handler),
        )
        results = await svc.find_recent_citing(
            ["openalex:W_SEED", "semantic_scholar:S_SEED"],
            year_from=2020,
            year_to=2030,
            limit=10,
        )

        # OA citation + S2's S_NEW (S_OLD dropped client-side).
        years = {p.year for p in results}
        assert 2024 in years
        assert 2001 not in years

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_find_highly_cited_neighbours_unions_three_lookups_and_ranks_log_citations(
        self, tmp_path
    ) -> None:
        """Verifies: find_highly_cited_neighbours() unions
        related + cited + citing, dedups, ranks by log(citation_count+1).
        """
        # The same DOI surfaces across all three lookups.
        shared_doi = "10.2/across"

        async def s2_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/recommendations/v1/papers" in url:
                return httpx.Response(
                    200,
                    json={
                        "recommendedPapers": [
                            _make_s2_paper(
                                "S_X", title="Big", doi=shared_doi, year=2020, citations=500
                            )
                        ]
                    },
                )
            if "/references" in url:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "citedPaper": _make_s2_paper(
                                    "S_REF", title="Tiny", doi="10.2/tiny", year=2010, citations=2
                                )
                            }
                        ]
                    },
                )
            if "/citations" in url:
                return httpx.Response(
                    200,
                    json={"data": []},
                )
            return httpx.Response(200, json={"data": []})

        async def oa_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "related_to:" in url:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            _make_oa_work(
                                "W_X", title="Big", doi=shared_doi, year=2020, citations=500
                            )
                        ]
                    },
                )
            if f"/works/W_SEED" in url:
                return httpx.Response(
                    200,
                    json=_make_oa_work("W_SEED", title="Seed", referenced_works=[]),
                )
            if "cites:" in url:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            _make_oa_work(
                                "W_CIT",
                                title="Citing-mid",
                                doi="10.2/mid",
                                year=2024,
                                citations=50,
                            )
                        ]
                    },
                )
            # Bulk references lookup — none.
            return httpx.Response(200, json={"results": []})

        svc = RelatedPaperService(
            _settings(tmp_path),
            openalex_client=_client(oa_handler),
            semantic_scholar_client=_client(s2_handler),
        )
        results = await svc.find_highly_cited_neighbours(
            ["openalex:W_SEED", "semantic_scholar:S_SEED"], limit=10
        )

        # Big appears once (merged across S2 recommendations + OA related_to).
        assert len([p for p in results if p.doi == shared_doi]) == 1
        # Sort: highest citations first.
        assert results[0].citation_count == 500
        # Order: 500 > 50 > 2.
        cit_counts = [p.citation_count for p in results]
        assert cit_counts == sorted(cit_counts, reverse=True)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_find_review_candidates_keeps_only_review_tokens(
        self, tmp_path
    ) -> None:
        """Verifies: find_review_candidates() retains papers whose
        title or abstract carries review/survey/overview/meta-analysis."""

        async def s2_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/recommendations/v1/papers" in url:
                return httpx.Response(
                    200,
                    json={
                        "recommendedPapers": [
                            _make_s2_paper(
                                "S_R", title="A Review of Foo", year=2022, reference_count=200, citations=80
                            ),
                            _make_s2_paper(
                                "S_N", title="Pure Research Article", year=2022, reference_count=10
                            ),
                            _make_s2_paper(
                                "S_M",
                                title="X",
                                abstract="A meta-analysis of related work.",
                                year=2021,
                                reference_count=150,
                                citations=40,
                            ),
                        ]
                    },
                )
            return httpx.Response(200, json={"data": []})

        async def oa_handler(request: httpx.Request) -> httpx.Response:
            # Empty OA side.
            url = str(request.url)
            if "/works/W_SEED" in url:
                return httpx.Response(
                    200, json=_make_oa_work("W_SEED", title="Seed", referenced_works=[])
                )
            return httpx.Response(200, json={"results": []})

        svc = RelatedPaperService(
            _settings(tmp_path),
            openalex_client=_client(oa_handler),
            semantic_scholar_client=_client(s2_handler),
        )
        results = await svc.find_review_candidates(
            ["semantic_scholar:S_SEED", "openalex:W_SEED"], limit=10
        )

        titles = [p.title for p in results]
        assert "A Review of Foo" in titles
        # Token in abstract counts too.
        assert any(p.semantic_scholar_id == "S_M" for p in results)
        # Non-review paper dropped.
        assert "Pure Research Article" not in titles
        # Sort: high reference count first.
        assert results[0].reference_count >= results[-1].reference_count

    @pytest.mark.unit
    def test_ranking_preset_highest_cited_sorts_by_citation_count_desc(self) -> None:
        """Verifies: 'most_cited' ranking sorts by log(citations+1) desc."""
        papers = [
            DiscoveredPaper(id="a", source="x", title="A", citation_count=10),
            DiscoveredPaper(id="b", source="x", title="B", citation_count=999),
            DiscoveredPaper(id="c", source="x", title="C", citation_count=0),
        ]
        sorted_papers = rank_papers(papers, ranking="most_cited")
        assert [p.id for p in sorted_papers] == ["b", "a", "c"]

    @pytest.mark.unit
    def test_ranking_preset_most_recent_sorts_by_year_desc(self) -> None:
        """Verifies: 'recent_and_relevant' ranking floats newer papers up."""
        papers = [
            DiscoveredPaper(id="old", source="x", title="Old", year=1999),
            DiscoveredPaper(id="new", source="x", title="New", year=2024),
            DiscoveredPaper(id="mid", source="x", title="Mid", year=2015),
        ]
        sorted_papers = rank_papers(papers, ranking="recent_and_relevant")
        assert [p.id for p in sorted_papers] == ["new", "mid", "old"]

    @pytest.mark.unit
    def test_ranking_preset_foundational_balances_citations_and_age(self) -> None:
        """Verifies: 'foundational' ranking rewards citations + older year."""
        papers = [
            DiscoveredPaper(id="new_low", source="x", title="X", year=2020, citation_count=5),
            DiscoveredPaper(id="old_high", source="x", title="Y", year=1990, citation_count=999),
            DiscoveredPaper(id="new_high", source="x", title="Z", year=2024, citation_count=999),
        ]
        sorted_papers = rank_papers(papers, ranking="foundational")
        # Both high-citation papers come first; older one wins among them.
        assert sorted_papers[0].id == "old_high"
        assert sorted_papers[1].id == "new_high"
        assert sorted_papers[2].id == "new_low"

    @pytest.mark.unit
    def test_is_review_candidate_matches_documented_tokens(self) -> None:
        """Verifies: review_candidates filter covers the closed token set."""
        for token in REVIEW_TOKENS:
            p = DiscoveredPaper(id="t", source="x", title=f"A {token} paper")
            assert is_review_candidate(p), f"missed token: {token!r}"
        # Negative case.
        assert not is_review_candidate(
            DiscoveredPaper(id="t", source="x", title="Original research")
        )

    @pytest.mark.unit
    def test_constants_pinned(self) -> None:
        """Verifies: RELATED_MODES + RANKING_PRESETS are the closed sets
        documented for the related-papers modes and rankings. Future changes
        have to update these constants and the test together."""
        assert RELATED_MODES == (
            "similar",
            "foundational",
            "recent_citing",
            "highly_cited_neighbours",
            "review_candidates",
        )
        assert RANKING_PRESETS == (
            "most_relevant",
            "most_cited",
            "recent_and_relevant",
            "foundational",
        )
