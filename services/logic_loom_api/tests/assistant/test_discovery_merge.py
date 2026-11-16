"""Tests for discovery result normalisation + merge.

Covers:
  1. Two providers returning the same DOI yield one merged result with both
     IDs populated.
  2. source field is 'merged' when at least two raw_dicts contributed.
"""

from __future__ import annotations

from typing import Any

import pytest

from logic_loom_api.assistant.discovery import (
    DiscoveredPaper,
    _normalize_title,
    merge_results,
    normalize_openalex,
    normalize_semantic_scholar,
)


def _openalex_fixture(
    *,
    work_id: str = "W12345",
    doi: str | None = "10.1234/example",
    title: str = "Graphene synthesis at low temperature",
    year: int = 2020,
) -> dict[str, Any]:
    """Minimal OpenAlex 'works' entry shaped like the real API response."""
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "title": title,
        "publication_year": year,
        "publication_date": f"{year}-06-01",
        "type": "journal-article",
        "cited_by_count": 7,
        "open_access": {"is_oa": True},
        "best_oa_location": {
            "pdf_url": "https://example.org/graphene.pdf",
            "landing_page_url": "https://example.org/graphene",
        },
        "primary_location": {
            "source": {"display_name": "Journal of Examples"},
        },
        "authorships": [
            {"author": {"display_name": "Alice Author"}},
            {"author": {"display_name": "Bob Builder"}},
        ],
        "abstract_inverted_index": {
            "Graphene": [0],
            "is": [1],
            "fun.": [2],
        },
        "ids": {"arxiv": "2001.12345"},
    }


def _s2_fixture(
    *,
    paper_id: str = "S2-99",
    doi: str | None = "10.1234/example",
    title: str = "Graphene synthesis at low temperature",
    year: int = 2020,
) -> dict[str, Any]:
    """Minimal Semantic Scholar 'paper' entry shaped like the real API."""
    return {
        "paperId": paper_id,
        "title": title,
        "abstract": "Graphene is fun.",
        "year": year,
        "publicationDate": f"{year}-06-01",
        "venue": "Journal of Examples",
        "citationCount": 7,
        "referenceCount": 25,
        "publicationTypes": ["JournalArticle"],
        "externalIds": {
            "DOI": doi,
            "ArXiv": "2001.12345",
            "PubMed": "32123456",
        },
        "authors": [
            {"name": "Alice Author"},
            {"name": "Bob Builder"},
        ],
        "openAccessPdf": {"url": "https://example.org/graphene.pdf"},
    }


class TestDiscoveryNormalisation:
    """Golden-file tests for the normalisers."""

    @pytest.mark.unit
    def test_normalize_openalex_to_discovered_paper(self) -> None:
        """Verifies: normalize_openalex returns a DiscoveredPaper with every
        documented field populated from the fixture."""
        paper = normalize_openalex(_openalex_fixture())

        assert isinstance(paper, DiscoveredPaper)
        assert paper.id == "openalex:W12345"
        assert paper.source == "openalex"
        assert paper.openalex_id == "W12345"
        assert paper.doi == "10.1234/example"
        assert paper.arxiv_id == "2001.12345"
        assert paper.title == "Graphene synthesis at low temperature"
        assert paper.year == 2020
        assert paper.publication_date == "2020-06-01"
        assert paper.work_type == "journal-article"
        assert paper.venue == "Journal of Examples"
        assert paper.authors == ["Alice Author", "Bob Builder"]
        assert paper.citation_count == 7
        assert paper.pdf_url == "https://example.org/graphene.pdf"
        assert paper.is_open_access is True
        # Inverted-index abstract is reconstructed in word order.
        assert paper.abstract == "Graphene is fun."
        assert "openalex" in paper.raw

    @pytest.mark.unit
    def test_normalize_semantic_scholar_to_discovered_paper(self) -> None:
        """Verifies: normalize_semantic_scholar returns a DiscoveredPaper
        with every documented field populated from the fixture."""
        paper = normalize_semantic_scholar(_s2_fixture())

        assert isinstance(paper, DiscoveredPaper)
        assert paper.id == "semantic_scholar:S2-99"
        assert paper.source == "semantic_scholar"
        assert paper.semantic_scholar_id == "S2-99"
        assert paper.doi == "10.1234/example"
        assert paper.arxiv_id == "2001.12345"
        assert paper.pmid == "32123456"
        assert paper.title == "Graphene synthesis at low temperature"
        assert paper.year == 2020
        assert paper.venue == "Journal of Examples"
        assert paper.authors == ["Alice Author", "Bob Builder"]
        assert paper.citation_count == 7
        assert paper.reference_count == 25
        assert paper.pdf_url == "https://example.org/graphene.pdf"
        assert paper.is_open_access is True
        assert paper.abstract == "Graphene is fun."
        assert "semantic_scholar" in paper.raw


class TestDiscoveryMerge:
    """Merge tests: same-DOI collapse and the ``merged`` source field."""

    @pytest.mark.unit
    def test_merge_by_doi_collapses_same_paper(self) -> None:
        """Verifies: Two providers returning the same DOI
        collapse to ONE merged DiscoveredPaper with both IDs populated and
        source='merged'."""
        oa = [normalize_openalex(_openalex_fixture(doi="10.1234/dup"))]
        s2 = [normalize_semantic_scholar(_s2_fixture(doi="10.1234/dup"))]

        result = merge_results(oa, s2)

        assert len(result) == 1
        merged = result[0]
        assert merged.source == "merged"
        # Both IDs populated.
        assert merged.openalex_id == "W12345"
        assert merged.semantic_scholar_id == "S2-99"
        assert merged.doi == "10.1234/dup"
        # Both raw dicts retained.
        assert set(merged.raw.keys()) == {"openalex", "semantic_scholar"}

    @pytest.mark.unit
    def test_merge_by_id_overlap_without_doi(self) -> None:
        """Verifies: when DOI is missing on one side but provider ids
        overlap via arXiv, the merge still fires."""
        # OpenAlex entry with no DOI, only arxiv id.
        oa_raw = _openalex_fixture(doi=None)
        oa_raw["doi"] = None
        oa = [normalize_openalex(oa_raw)]
        # S2 entry with same arxiv id, no DOI.
        s2_raw = _s2_fixture(doi=None)
        s2_raw["externalIds"] = {"ArXiv": "2001.12345"}
        s2 = [normalize_semantic_scholar(s2_raw)]

        result = merge_results(oa, s2)

        assert len(result) == 1
        merged = result[0]
        assert merged.source == "merged"
        assert merged.arxiv_id == "2001.12345"

    @pytest.mark.unit
    def test_merge_by_normalised_title_and_year_when_no_id_overlap(self) -> None:
        """Verifies: when neither DOI nor IDs match, normalised title+year
        still triggers the merge."""
        # OpenAlex: no DOI, no arxiv/pubmed, just a title + year.
        oa_raw = _openalex_fixture(doi=None)
        oa_raw["doi"] = None
        oa_raw["ids"] = {}  # no arxiv
        oa = [normalize_openalex(oa_raw)]
        # S2: same title + year, no DOI, no arxiv/pubmed.
        s2_raw = _s2_fixture(doi=None)
        s2_raw["externalIds"] = {}
        s2 = [normalize_semantic_scholar(s2_raw)]

        result = merge_results(oa, s2)

        assert len(result) == 1
        merged = result[0]
        assert merged.source == "merged"
        assert merged.title == "Graphene synthesis at low temperature"

    @pytest.mark.unit
    def test_merged_paper_keeps_both_raw_dicts(self) -> None:
        """Verifies: implementation step 3 — merged paper carries both raw
        provider responses under .raw, keyed by provider name."""
        oa = [normalize_openalex(_openalex_fixture())]
        s2 = [normalize_semantic_scholar(_s2_fixture())]

        result = merge_results(oa, s2)

        assert len(result) == 1
        raws = result[0].raw
        assert "openalex" in raws
        assert "semantic_scholar" in raws
        assert raws["openalex"]["id"] == "https://openalex.org/W12345"
        assert raws["semantic_scholar"]["paperId"] == "S2-99"

    @pytest.mark.unit
    def test_no_merge_when_only_one_provider_returns_paper(self) -> None:
        """Verifies (corollary): unique results pass through with
        their original provider name (not 'merged')."""
        oa_only = [
            normalize_openalex(
                _openalex_fixture(work_id="W-A", doi="10/a", title="Alpha")
            )
        ]
        s2_only = [
            normalize_semantic_scholar(
                _s2_fixture(paper_id="S2-B", doi="10/b", title="Beta")
            )
        ]

        result = merge_results(oa_only, s2_only)

        assert len(result) == 2
        # Order is deterministic in input order.
        sources = [p.source for p in result]
        assert sources == ["openalex", "semantic_scholar"]
        assert {p.title for p in result} == {"Alpha", "Beta"}
        # raw carries exactly one provider key each.
        for p in result:
            assert len(p.raw) == 1


class TestDiscoveryMergeHelpers:
    """Centralised helpers used by both merger and Zotero matcher."""

    @pytest.mark.unit
    def test_normalize_title_lowercases_strips_nonalphanum_collapses_whitespace(
        self,
    ) -> None:
        """Verifies: _normalize_title rule (lowercase, strip non-alnum,
        collapse whitespace). Same rule the Zotero catalog matcher uses."""
        assert (
            _normalize_title("A: Quick--Test!  Of  Titles")
            == "a quick test of titles"
        )
        assert _normalize_title("") == ""
        assert _normalize_title(None) == ""
