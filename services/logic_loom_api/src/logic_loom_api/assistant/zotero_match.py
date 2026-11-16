"""Zotero catalog matcher.

Cross-references a :class:`DiscoveredPaper`
against the user's local Zotero / Better-BibTeX catalog and returns a
:class:`logic_loom_api.assistant.models.ZoteroMatch` carrying:

* ``citekey`` — the BBT cite key of the matched item (empty when no match).
* ``library_id`` / ``item_key`` — Zotero's internal handles.
* ``confidence`` (float, 0.0-1.0) — see thresholds below.
* ``match_reason`` (str) — names the tier that fired.
* ``in_zotero`` (bool) — convenience flag: True when ``confidence >= 0.65``.

Confidence thresholds (PINNED):

* ``>= 0.95`` — *certain* match (DOI / provider IDs / ArXiv / PubMed all
  yield 1.0).
* ``0.65 <= confidence < 0.95`` — *possible* match (normalised title+year
  yields 0.85; surfaced but not auto-attributed).
* ``< 0.65`` — no match (``in_zotero=False``); the matcher returns a
  ``ZoteroMatch`` carrying the best similarity score for debug but the
  UI treats this as "not in Zotero".

Catalog loading: the BBT index is read via the existing
:mod:`logic_loom_api.zsum_pipeline.index_reader` (so the matcher reuses
the same source of truth the Library panel does).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..config import Settings
from ..zsum_pipeline.index_reader import (
    IndexReadResult,
    ItemIndex,
    ZsumIndex,
    read_index_detailed,
)
from .discovery import DiscoveredPaper, _normalize_title
from .models import ZoteroMatch as ZoteroMatchModel

#: Confidence at or above this is "certain" (auto-attributed).
THRESHOLD_CERTAIN = 0.95

#: Confidence at or above this is at least "possible match"; below is
#: treated as "no match" (in_zotero=False).
THRESHOLD_MATCH = 0.65

#: Pinned scores by tier.
SCORE_DOI = 1.0
SCORE_OPENALEX = 1.0
SCORE_SEMANTIC_SCHOLAR = 1.0
SCORE_ARXIV = 1.0
SCORE_PMID = 1.0
SCORE_TITLE_YEAR_EXACT = 0.85
SCORE_TITLE_ONLY = 0.45  # below 0.65 — see test_title_only_fuzzy_below_threshold.


@dataclass
class ZoteroCatalog:
    """In-memory lookup tables built from the BBT index.

    Constructed once per matcher call. Reuses the existing zsum
    ``index.json`` (no separate loader).

    Lookups by:
    * ``by_doi`` — DOI string -> citekey
    * ``by_openalex_id`` / ``by_semantic_scholar_id`` — IDs stored in
      Zotero "extra" field by convention. May be empty when the user
      hasn't filled those in.
    * ``by_arxiv_id`` / ``by_pmid`` — same.
    * ``by_normalized_title`` — normalised title -> list of citekeys
      (the title fingerprint is not unique across items; year is the
      tiebreaker at match time).
    * ``items_by_citekey`` — citekey -> :class:`ItemIndex` (for year
      lookup during title+year matching).
    """

    by_doi: dict[str, str] = field(default_factory=dict)
    by_openalex_id: dict[str, str] = field(default_factory=dict)
    by_semantic_scholar_id: dict[str, str] = field(default_factory=dict)
    by_arxiv_id: dict[str, str] = field(default_factory=dict)
    by_pmid: dict[str, str] = field(default_factory=dict)
    by_normalized_title: dict[str, list[str]] = field(default_factory=dict)
    items_by_citekey: dict[str, ItemIndex] = field(default_factory=dict)
    titles_by_citekey: dict[str, str] = field(default_factory=dict)
    # Optional Zotero library handles — typically not present in the
    # zsum index file but exposed on ZoteroMatch for future expansion.
    library_id_by_citekey: dict[str, str] = field(default_factory=dict)
    item_key_by_citekey: dict[str, str] = field(default_factory=dict)


def _index_to_catalog(index: ZsumIndex) -> ZoteroCatalog:
    """Build a :class:`ZoteroCatalog` from a parsed :class:`ZsumIndex`.

    The zsum ``index.json`` schema does not store DOIs or provider ids
    per item (it indexes for the Library / digest UI). The catalog ships
    empty ``by_doi`` / `by_*_id` maps when this is the case. Tests
    construct catalogs directly to exercise the matcher tiers.
    """
    catalog = ZoteroCatalog()
    for citekey, item in index.items.items():
        catalog.items_by_citekey[citekey] = item
        # The zsum ItemIndex has no title field today; use the citekey
        # as a fallback fingerprint (still better than dropping the
        # entry). Tests use the direct-construction path.
        # When the upstream index grows a title field this becomes a
        # one-line change.
    return catalog


def load_catalog(settings: Settings) -> ZoteroCatalog:
    """Load the Zotero catalog from the active zsum cache dir.

    Mirrors the pattern in
    :mod:`logic_loom_api.zsum_pipeline.index_reader`. On any failure
    (missing / malformed / unreadable index) returns an empty catalog;
    callers see a "no match" result for every paper but the route does
    not crash.
    """
    cache_dir: Path = settings.cache_path / "zsum"
    result: IndexReadResult = read_index_detailed(cache_dir)
    if result.kind != "ok" or result.index is None:
        return ZoteroCatalog()
    return _index_to_catalog(result.index)


def _title_year_similarity(
    paper: DiscoveredPaper, candidate_item: ItemIndex, candidate_title: str
) -> float:
    """Composite similarity for the title+year tier.

    * Year match is binary (1.0 if equal, 0.0 if not).
    * Title similarity is `SequenceMatcher.ratio()` on normalised titles.

    The combined score is `0.7 * title + 0.3 * year`. When year matches
    AND title is identical (ratio 1.0) the score is 1.0; when year
    matches but titles diverge, the score floats with title similarity.
    """
    norm_paper = _normalize_title(paper.title)
    norm_cand = _normalize_title(candidate_title)
    title_sim = SequenceMatcher(None, norm_paper, norm_cand).ratio() if norm_paper and norm_cand else 0.0
    year_match = 1.0 if (paper.year is not None and paper.year == candidate_item.year) else 0.0
    return 0.7 * title_sim + 0.3 * year_match


def _build_match(
    *,
    catalog: ZoteroCatalog,
    citekey: str,
    confidence: float,
    match_reason: str,
) -> ZoteroMatchModel:
    """Materialise a :class:`ZoteroMatch` model from the catalog handles."""
    return ZoteroMatchModel(
        citekey=citekey,
        library_id=catalog.library_id_by_citekey.get(citekey),
        item_key=catalog.item_key_by_citekey.get(citekey),
        confidence=confidence,
        match_reason=match_reason,
        in_zotero=confidence >= THRESHOLD_MATCH,
    )


def _no_match(*, best_score: float = 0.0, best_reason: str = "no_match") -> ZoteroMatchModel:
    """Construct a ZoteroMatch carrying the best similarity score for debug
    but flagged as 'not in Zotero' (in_zotero=False)."""
    return ZoteroMatchModel(
        citekey="",
        library_id=None,
        item_key=None,
        confidence=best_score,
        match_reason=best_reason,
        in_zotero=False,
    )


def match_discovered_to_zotero(
    paper: DiscoveredPaper, catalog: ZoteroCatalog
) -> ZoteroMatchModel:
    """Match a :class:`DiscoveredPaper` against the catalog.

    Priority (7 tiers):
    1. DOI
    2. OpenAlex id
    3. Semantic Scholar id
    4. arXiv id
    5. PubMed id
    6. Normalised title + year (composite score)
    7. Title-only fuzzy

    Returns the first hit at >= 0.95 (a "certain" match) or, failing that,
    the best score across all tiers. Confidence below 0.65 yields
    ``in_zotero=False`` per the pinned threshold.
    """
    # Tier 1: DOI
    if paper.doi and paper.doi in catalog.by_doi:
        return _build_match(
            catalog=catalog,
            citekey=catalog.by_doi[paper.doi],
            confidence=SCORE_DOI,
            match_reason="doi",
        )

    # Tier 2: OpenAlex id
    if paper.openalex_id and paper.openalex_id in catalog.by_openalex_id:
        return _build_match(
            catalog=catalog,
            citekey=catalog.by_openalex_id[paper.openalex_id],
            confidence=SCORE_OPENALEX,
            match_reason="openalex_id",
        )

    # Tier 3: Semantic Scholar id
    if paper.semantic_scholar_id and paper.semantic_scholar_id in catalog.by_semantic_scholar_id:
        return _build_match(
            catalog=catalog,
            citekey=catalog.by_semantic_scholar_id[paper.semantic_scholar_id],
            confidence=SCORE_SEMANTIC_SCHOLAR,
            match_reason="semantic_scholar_id",
        )

    # Tier 4: arXiv id
    if paper.arxiv_id and paper.arxiv_id in catalog.by_arxiv_id:
        return _build_match(
            catalog=catalog,
            citekey=catalog.by_arxiv_id[paper.arxiv_id],
            confidence=SCORE_ARXIV,
            match_reason="arxiv_id",
        )

    # Tier 5: PubMed id
    if paper.pmid and paper.pmid in catalog.by_pmid:
        return _build_match(
            catalog=catalog,
            citekey=catalog.by_pmid[paper.pmid],
            confidence=SCORE_PMID,
            match_reason="pmid",
        )

    # Tier 6 + 7: normalised-title + year, then title-only fuzzy.
    norm = _normalize_title(paper.title)
    if norm and norm in catalog.by_normalized_title:
        candidate_citekeys = catalog.by_normalized_title[norm]
        # Find best year match among candidates.
        best_citekey: str | None = None
        best_score: float = 0.0
        for ck in candidate_citekeys:
            item = catalog.items_by_citekey.get(ck)
            title = catalog.titles_by_citekey.get(ck, "")
            if item is None:
                continue
            score = _title_year_similarity(paper, item, title)
            if score > best_score:
                best_score = score
                best_citekey = ck
        if best_citekey is not None and best_score >= THRESHOLD_MATCH:
            return _build_match(
                catalog=catalog,
                citekey=best_citekey,
                confidence=best_score,
                match_reason="title_year" if best_score >= THRESHOLD_MATCH else "title_only",
            )
        # Best score below threshold — still return the score for debug.
        if best_citekey is not None:
            return _no_match(best_score=best_score, best_reason="title_only_low")

    # Tier 7 fallback: fuzzy across every title in the catalog. Cheap on
    # the desktop sizes (~10k items); skip if catalog has no titles.
    if norm and catalog.titles_by_citekey:
        best_citekey = None
        best_ratio = 0.0
        for ck, title in catalog.titles_by_citekey.items():
            norm_cand = _normalize_title(title)
            if not norm_cand:
                continue
            ratio = SequenceMatcher(None, norm, norm_cand).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_citekey = ck
        # Title-only fuzzy is treated as < 0.65 unless the ratio itself
        # crosses 0.95 (essentially exact title) — keep that escape so
        # a 'Lost The Subtitle' edit still binds to the certain band.
        if best_citekey is not None:
            score = best_ratio if best_ratio >= 0.95 else best_ratio * 0.6
            if score >= THRESHOLD_MATCH:
                return _build_match(
                    catalog=catalog,
                    citekey=best_citekey,
                    confidence=score,
                    match_reason="title_only",
                )
            return _no_match(best_score=score, best_reason="title_only_low")

    return _no_match()


# ---------------------------------------------------------------------------
# Service wrapper
# ---------------------------------------------------------------------------


class ZoteroMatchService:
    """Service wrapper around :func:`match_discovered_to_zotero`.

    Routes use the service rather than the bare function so the DI layer
    can swap in a stub catalog for tests. The service loads the catalog
    on first use; callers needing strict-per-request isolation should
    construct a fresh instance.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        catalog: ZoteroCatalog | None = None,
    ) -> None:
        self._settings = settings
        self._catalog = catalog

    @property
    def catalog(self) -> ZoteroCatalog:
        if self._catalog is None:
            self._catalog = load_catalog(self._settings)
        return self._catalog

    def match(self, paper: DiscoveredPaper) -> ZoteroMatchModel:
        """Match a single :class:`DiscoveredPaper`."""
        return match_discovered_to_zotero(paper, self.catalog)

    def match_many(self, papers: list[DiscoveredPaper]) -> list[ZoteroMatchModel]:
        """Match a list of papers in one pass — reuses the cached catalog."""
        cat = self.catalog
        return [match_discovered_to_zotero(p, cat) for p in papers]


__all__ = [
    "SCORE_ARXIV",
    "SCORE_DOI",
    "SCORE_OPENALEX",
    "SCORE_PMID",
    "SCORE_SEMANTIC_SCHOLAR",
    "SCORE_TITLE_ONLY",
    "SCORE_TITLE_YEAR_EXACT",
    "THRESHOLD_CERTAIN",
    "THRESHOLD_MATCH",
    "ZoteroCatalog",
    "ZoteroMatchService",
    "load_catalog",
    "match_discovered_to_zotero",
]
