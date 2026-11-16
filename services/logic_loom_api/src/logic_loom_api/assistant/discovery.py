"""Paper discovery service.

Implements:

* :class:`DiscoveredPaper` — the canonical Pydantic model returned by the
  Discovery surface. Carries provider IDs
  (OpenAlex, Semantic Scholar, DOI, arXiv, PubMed), bibliographic
  metadata, PDF location hints, and a ``source`` discriminator
  (``"openalex"`` / ``"semantic_scholar"`` / ``"merged"``).
* :func:`normalize_openalex` — convert a raw OpenAlex ``works`` response
  entry into a :class:`DiscoveredPaper`.
* :func:`normalize_semantic_scholar` — same for an S2 ``paper`` response.
* :func:`merge_results` — union by DOI first, then ID overlap, then
  normalised ``(title, year)``. Merged papers carry ``source="merged"``
  and retain both raw response dicts under ``raw``.
* :class:`PaperDiscoveryService` — orchestrator that wraps the provider
  clients, calls the normalisers, and emits the merged list. The HTTP
  routes consume this service via the FastAPI dep wrapper.

Design constraint: the merger is **deterministic** — same input always
yields the same output (order, ids, source). Tests rely on this for
golden-file comparisons.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

from ..config import Settings
from .providers.openalex import OpenAlexClient, ProviderSearchFilters
from .providers.semantic_scholar import SemanticScholarClient

#: ``source`` field literals on :class:`DiscoveredPaper`. ``"merged"`` is
#: produced by :func:`merge_results` when two or more provider responses
#: collapse to one paper.
DiscoveredPaperSource = str  # Literal narrow not used — kept open for new sources.


class DiscoveredPaper(BaseModel):
    """One paper returned by Discovery.

    ``id`` is the
    internal canonical id (``"<provider>:<provider_id>"``); the provider-
    specific ids live on ``openalex_id`` / ``semantic_scholar_id`` /
    ``doi`` / ``arxiv_id`` / ``pmid`` so downstream code can cross-link
    without parsing.

    ``raw`` carries the original response dicts keyed by provider name
    — useful for debugging and for re-ranking.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    source: str  # "openalex" | "semantic_scholar" | "merged"

    # Identifiers (every optional — at least one of {doi, openalex_id,
    # semantic_scholar_id, arxiv_id, pmid} is typically populated).
    doi: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None

    # Bibliographic metadata.
    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    publication_date: str | None = None
    work_type: str | None = None

    # Metrics.
    citation_count: int | None = None
    reference_count: int | None = None

    # PDF availability.
    pdf_url: str | None = None
    is_open_access: bool | None = None

    # Raw provider responses, keyed by provider name. Merged papers carry
    # both keys; single-provider papers carry one.
    raw: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _strip_openalex_prefix(value: str | None) -> str | None:
    """OpenAlex returns ids as ``https://openalex.org/W12345``; the canonical
    short form is ``W12345``. Same trick for sources / authors / venues."""
    if not value:
        return None
    if value.startswith("https://openalex.org/"):
        return value.rsplit("/", 1)[-1]
    return value


def _join_openalex_abstract(inv_index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex returns abstracts as an inverted index (``word -> positions``)
    to satisfy a publisher request. Reassemble into a single string."""
    if not inv_index:
        return None
    positions: list[tuple[int, str]] = []
    for token, idx_list in inv_index.items():
        for idx in idx_list:
            positions.append((idx, token))
    positions.sort()
    return " ".join(tok for _, tok in positions) or None


def normalize_openalex(raw: dict[str, Any]) -> DiscoveredPaper:
    """Convert an OpenAlex ``works`` entry into a :class:`DiscoveredPaper`."""
    openalex_id = _strip_openalex_prefix(raw.get("id"))
    doi = raw.get("doi")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/") :]

    authors: list[str] = []
    for authorship in raw.get("authorships") or []:
        name = (authorship.get("author") or {}).get("display_name")
        if name:
            authors.append(name)

    venue = None
    primary_location = raw.get("primary_location") or {}
    source_block = primary_location.get("source") or {}
    if source_block:
        venue = source_block.get("display_name")

    pdf_url = None
    best_oa = raw.get("best_oa_location") or {}
    if best_oa:
        pdf_url = best_oa.get("pdf_url") or best_oa.get("landing_page_url")

    # OpenAlex puts arXiv ids in ids.arxiv / external_ids.
    ids_block = raw.get("ids") or {}
    arxiv_id = ids_block.get("arxiv") or None
    pmid = ids_block.get("pmid")
    if pmid and isinstance(pmid, str) and pmid.startswith("https://"):
        pmid = pmid.rsplit("/", 1)[-1]

    canonical_id = f"openalex:{openalex_id}" if openalex_id else f"doi:{doi}" if doi else "openalex:unknown"

    return DiscoveredPaper(
        id=canonical_id,
        source="openalex",
        doi=doi,
        openalex_id=openalex_id,
        semantic_scholar_id=None,
        arxiv_id=arxiv_id,
        pmid=pmid,
        title=raw.get("title") or raw.get("display_name") or "",
        abstract=_join_openalex_abstract(raw.get("abstract_inverted_index")),
        authors=authors,
        year=raw.get("publication_year"),
        venue=venue,
        publication_date=raw.get("publication_date"),
        work_type=raw.get("type"),
        citation_count=raw.get("cited_by_count"),
        reference_count=None,
        pdf_url=pdf_url,
        is_open_access=(raw.get("open_access") or {}).get("is_oa"),
        raw={"openalex": raw},
    )


def normalize_semantic_scholar(raw: dict[str, Any]) -> DiscoveredPaper:
    """Convert a Semantic Scholar ``paper`` entry into a :class:`DiscoveredPaper`."""
    paper_id = raw.get("paperId")
    external = raw.get("externalIds") or {}
    doi = external.get("DOI") or raw.get("doi")
    arxiv_id = external.get("ArXiv")
    pmid = external.get("PubMed")

    authors_block = raw.get("authors") or []
    authors = [a.get("name") for a in authors_block if a.get("name")]

    pub_types = raw.get("publicationTypes") or []
    work_type = pub_types[0] if pub_types else None

    pdf_url = None
    open_pdf = raw.get("openAccessPdf") or {}
    if open_pdf:
        pdf_url = open_pdf.get("url")

    canonical_id = (
        f"semantic_scholar:{paper_id}" if paper_id else f"doi:{doi}" if doi else "semantic_scholar:unknown"
    )

    return DiscoveredPaper(
        id=canonical_id,
        source="semantic_scholar",
        doi=doi,
        openalex_id=None,
        semantic_scholar_id=paper_id,
        arxiv_id=arxiv_id,
        pmid=pmid,
        title=raw.get("title") or "",
        abstract=raw.get("abstract"),
        authors=authors,
        year=raw.get("year"),
        venue=raw.get("venue"),
        publication_date=raw.get("publicationDate"),
        work_type=work_type,
        citation_count=raw.get("citationCount"),
        reference_count=raw.get("referenceCount"),
        pdf_url=pdf_url,
        is_open_access=bool(pdf_url) if pdf_url is not None else None,
        raw={"semantic_scholar": raw},
    )


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


_TITLE_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_title(title: str | None) -> str:
    """Lowercase, strip non-alphanumeric, collapse whitespace.

    Centralised here so the Zotero matcher and the merger see the same
    fingerprint. Empty / None inputs yield ``""``.
    """
    if not title:
        return ""
    lowered = title.lower()
    return _TITLE_NONALNUM_RE.sub(" ", lowered).strip()


def _id_match_key(paper: DiscoveredPaper) -> str | None:
    """Return a stable string usable for ID-overlap merging when DOI is
    absent. None if no provider-specific id is set."""
    for value in (
        paper.openalex_id,
        paper.semantic_scholar_id,
        paper.arxiv_id,
        paper.pmid,
    ):
        if value:
            return value
    return None


def _merge_pair(primary: DiscoveredPaper, other: DiscoveredPaper) -> DiscoveredPaper:
    """Merge ``other`` into ``primary``. Identifiers from ``other`` fill in
    blanks on ``primary``; metadata sticks with whichever source has it.
    Both raw dicts are kept under ``raw``."""
    merged_raw = {**primary.raw, **other.raw}
    return primary.model_copy(
        update={
            "source": "merged",
            "doi": primary.doi or other.doi,
            "openalex_id": primary.openalex_id or other.openalex_id,
            "semantic_scholar_id": primary.semantic_scholar_id or other.semantic_scholar_id,
            "arxiv_id": primary.arxiv_id or other.arxiv_id,
            "pmid": primary.pmid or other.pmid,
            "abstract": primary.abstract or other.abstract,
            "authors": primary.authors or other.authors,
            "year": primary.year if primary.year is not None else other.year,
            "venue": primary.venue or other.venue,
            "publication_date": primary.publication_date or other.publication_date,
            "work_type": primary.work_type or other.work_type,
            "citation_count": (
                primary.citation_count
                if primary.citation_count is not None
                else other.citation_count
            ),
            "reference_count": (
                primary.reference_count
                if primary.reference_count is not None
                else other.reference_count
            ),
            "pdf_url": primary.pdf_url or other.pdf_url,
            "is_open_access": (
                primary.is_open_access
                if primary.is_open_access is not None
                else other.is_open_access
            ),
            "raw": merged_raw,
        }
    )


def merge_results(*provider_lists: list[DiscoveredPaper]) -> list[DiscoveredPaper]:
    """Merge papers across providers.

    Tiers:

    1. DOI overlap — same DOI collapses to one.
    2. Provider-id overlap when DOI is missing (openalex_id /
       semantic_scholar_id / arxiv_id / pmid).
    3. Normalised title + year match when neither DOI nor IDs match.

    Output is deterministic in the order of inputs: the first paper that
    seeds a given identity wins as the merge primary. Subsequent papers
    sharing the identity merge into it.

    Each entry in the output list carries ``source="merged"`` iff two or
    more raw response dicts contributed; otherwise the entry retains its
    single-provider source.
    """
    merged: list[DiscoveredPaper] = []
    by_doi: dict[str, int] = {}
    by_id: dict[str, int] = {}
    by_title_year: dict[tuple[str, int | None], int] = {}

    for provider_list in provider_lists:
        for paper in provider_list:
            primary_idx: int | None = None

            if paper.doi:
                primary_idx = by_doi.get(paper.doi)
            if primary_idx is None:
                id_key = _id_match_key(paper)
                if id_key:
                    primary_idx = by_id.get(id_key)
            if primary_idx is None:
                title_key = (_normalize_title(paper.title), paper.year)
                if title_key[0]:
                    primary_idx = by_title_year.get(title_key)

            if primary_idx is None:
                merged.append(paper)
                new_idx = len(merged) - 1
                if paper.doi:
                    by_doi.setdefault(paper.doi, new_idx)
                id_key = _id_match_key(paper)
                if id_key:
                    by_id.setdefault(id_key, new_idx)
                norm_title = _normalize_title(paper.title)
                if norm_title:
                    by_title_year.setdefault((norm_title, paper.year), new_idx)
                continue

            # Merge into the existing primary.
            primary = merged[primary_idx]
            combined = _merge_pair(primary, paper)
            merged[primary_idx] = combined
            # Index any new identifiers the merge picked up so future
            # entries can find this primary via any of them.
            if combined.doi and combined.doi not in by_doi:
                by_doi[combined.doi] = primary_idx
            new_id_key = _id_match_key(combined)
            if new_id_key and new_id_key not in by_id:
                by_id[new_id_key] = primary_idx
            norm_title = _normalize_title(combined.title)
            if norm_title:
                by_title_year.setdefault((norm_title, combined.year), primary_idx)

    return merged


# ---------------------------------------------------------------------------
# Service orchestrator
# ---------------------------------------------------------------------------


class PaperDiscoveryService:
    """Discovery orchestrator — provider clients + normalisers + merge.

    Constructed per-request (cheap). Real HTTP only happens when
    :meth:`search` is called. Provider clients can be injected for
    tests; otherwise they are built from the active :class:`Settings`.

    The HTTP routes consume this service via FastAPI ``Depends``;
    callers do not interact with the provider clients directly.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        openalex: OpenAlexClient | None = None,
        semantic_scholar: SemanticScholarClient | None = None,
    ) -> None:
        self._settings = settings
        self._openalex = openalex
        self._semantic_scholar = semantic_scholar

    @property
    def openalex(self) -> OpenAlexClient:
        if self._openalex is None:
            self._openalex = OpenAlexClient(self._settings)
        return self._openalex

    @property
    def semantic_scholar(self) -> SemanticScholarClient:
        if self._semantic_scholar is None:
            self._semantic_scholar = SemanticScholarClient(self._settings)
        return self._semantic_scholar

    async def search(
        self,
        query: str,
        *,
        filters: ProviderSearchFilters | None = None,
        limit: int = 25,
    ) -> list[DiscoveredPaper]:
        """Search both providers, normalise, and merge into a single list.

        Tolerates per-provider failures: if one provider raises a
        :class:`ProviderError` (rate limit, timeout, network error) the
        other's results are still returned. Both failing propagates the
        first error so the route layer can surface it. Provider errors are
        logged so the user can see which side degraded.
        """
        import asyncio

        oa_task = asyncio.create_task(
            self.openalex.search(query, filters=filters, limit=limit)
        )
        s2_task = asyncio.create_task(
            self.semantic_scholar.search(query, filters=filters, limit=limit)
        )
        oa_result, s2_result = await asyncio.gather(
            oa_task, s2_task, return_exceptions=True
        )

        oa_papers: list[DiscoveredPaper] = []
        s2_papers: list[DiscoveredPaper] = []

        if isinstance(oa_result, BaseException):
            logger.warning("OpenAlex search failed: %s", oa_result)
        else:
            oa_papers = [
                normalize_openalex(entry) for entry in (oa_result.get("results") or [])
            ]

        if isinstance(s2_result, BaseException):
            logger.warning("Semantic Scholar search failed: %s", s2_result)
        else:
            s2_papers = [
                normalize_semantic_scholar(entry) for entry in (s2_result.get("data") or [])
            ]

        # If both providers failed, surface whichever error came first.
        if isinstance(oa_result, BaseException) and isinstance(s2_result, BaseException):
            raise oa_result

        return merge_results(oa_papers, s2_papers)

    def get_provider_status(self) -> dict[str, Any]:
        """Return a snapshot of provider configuration (no network calls).

        Used by the route layer + UI to surface "Discovery is configured /
        not configured" without hitting the upstream APIs. Cache stats are
        served separately by the discover routes' ``/cache/stats`` endpoint.
        """
        s = self._settings
        return {
            "openalex": {
                "polite_pool": bool(s.openalex_user_email),
                "premium_key": bool(s.openalex_api_key),
            },
            "semantic_scholar": {
                "api_key": bool(s.semantic_scholar_api_key),
            },
        }


__all__ = [
    "DiscoveredPaper",
    "PaperDiscoveryService",
    "_normalize_title",
    "merge_results",
    "normalize_openalex",
    "normalize_semantic_scholar",
]
