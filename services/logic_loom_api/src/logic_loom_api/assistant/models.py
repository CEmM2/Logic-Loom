"""Assistant source-batch Pydantic models.

The `SourceBatch` here is intentionally a **superset** of the AKMS-bundle
:class:`logic_loom_api.batches.models.EditableBatch`:

* Assistant batches carry multi-kind sources (Zotero records, discovered
  papers, external PDFs, URLs, manual entries) — the AKMS model only has
  an opaque `papers: list[str]` field.
* Assistant batches do not require `nodes` or `generation` blocks; the
  AKMS model requires both (with defaults).
* `prompt_profile_id` / `prompt_overrides` / `notebook_id` /
  `default_workflow_id` are shared with the AKMS model, so they round-trip
  cleanly through :meth:`SourceBatch.to_editable_batch`.

The conversion contract is one-way: ``send_to_akms`` uses
:meth:`to_editable_batch` to write an :class:`EditableBatch` into the AKMS
workspace. There is no reverse converter — once an Assistant batch is
"sent" it lives independently in the AKMS workspace.

Schema stability: every field has a deterministic default (default-factory
or scalar) so ``SourceBatch.model_validate(SourceBatch(...).model_dump())``
is the identity transformation — required for the round-trip test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..batches.models import EditableBatch

SCHEMA_VERSION = 1

#: Allowed values for :attr:`BatchSourceItem.kind`. Closed Literal — adding
#: a new kind is a deliberate schema decision, not a drive-by change.
SourceItemKind = Literal["zotero", "discovered", "external_pdf", "url", "manual"]

#: Allowed values for :attr:`SourceBatch.status`. Matches the AKMS-bundle
#: :data:`logic_loom_api.batches.models.BatchStatus` so the two systems
#: agree on lifecycle states.
SourceBatchStatus = Literal["draft", "active", "archived"]


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Centralised so tests can monkeypatch a single call site if needed and
    so the field default is keyword-compatible with the AKMS-bundle
    :func:`logic_loom_api.batches.models._utc_now_iso` helper.
    """
    return datetime.now(UTC).isoformat()


class ZoteroMatch(BaseModel):
    """Zotero match metadata on a :class:`BatchSourceItem`.

    Shape evolution:

    * The minimal shape (``citekey``, ``library_id``, ``item_key``) lets
      :class:`BatchSourceItem.zotero_match` be a concrete type instead of
      ``dict[str, Any]``.
    * ``confidence`` / ``match_reason`` / ``in_zotero`` were added later
      — all defaulted so existing batches deserialise unchanged (a
      round-trip test asserts this).

    Pinned thresholds:

    * ``confidence >= 0.95`` — certain match (auto-attributed).
    * ``0.65 <= confidence < 0.95`` — possible match (surfaced but not
      auto-attributed).
    * ``confidence < 0.65`` — no match (``in_zotero=False``).
    """

    model_config = ConfigDict(extra="ignore")

    citekey: str
    library_id: str | None = None
    item_key: str | None = None
    # Catalog-match fields (all defaulted for backward-compat).
    confidence: float = 0.0
    match_reason: str = ""
    in_zotero: bool = False


class BatchSourceItem(BaseModel):
    """One source attached to a :class:`SourceBatch`.

    ``kind`` discriminates how downstream code resolves
    the canonical paper record (Zotero lookup vs. discovery cache vs.
    direct PDF / URL). Only ``id``, ``kind`` and ``title`` are required;
    every other field is optional so the same model handles all five
    source kinds without per-kind variants.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: SourceItemKind
    citekey: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    local_pdf_path: str | None = None
    external_pdf_url: str | None = None
    url: str | None = None
    zotero_match: ZoteroMatch | None = None


class SourceBatch(BaseModel):
    """Assistant-side editable source batch.

    Superset of :class:`logic_loom_api.batches.models.EditableBatch` — see
    module docstring. ``id`` is a stable slug derived from ``title`` by
    the registry (which mints it via
    :meth:`logic_loom_api.batches.store.BatchStore.new_id_from_title` for
    consistency with the AKMS-bundle store).

    ``created_at`` / ``updated_at`` are ISO-8601 strings (not
    :class:`datetime`) so round-trip JSON is lossless and the on-disk
    ``batches.json`` is human-diffable. Both default to "now" — the
    registry overwrites ``updated_at`` on every mutation.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    id: str
    title: str
    description: str = ""
    status: SourceBatchStatus = "active"
    tags: list[str] = Field(default_factory=list)
    purpose: str | None = None

    sources: list[BatchSourceItem] = Field(default_factory=list)
    notebook_id: str | None = None

    default_workflow_id: str | None = None
    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)

    akms_node_plan: list[dict[str, Any]] = Field(default_factory=list)

    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)

    # ---------------------------- AKMS conversion ----------------------------

    def to_editable_batch(self) -> EditableBatch:
        """Project this batch onto the AKMS-bundle :class:`EditableBatch`.

        Conversion rules:

        * ``id``, ``title``, ``description``, ``status``, ``created_at``,
          ``updated_at``, ``notebook_id``, ``default_workflow_id``,
          ``prompt_profile_id``, ``prompt_overrides`` map 1:1.
        * ``sources`` → ``papers`` as a list of preferred-identifier
          strings (see :func:`_source_paper_ref`). The Assistant retains
          the full :class:`BatchSourceItem` records; the AKMS-side
          workflow only needs an opaque paper ref.
        * ``tags``, ``purpose``, ``akms_node_plan`` are Assistant-only;
          they are dropped on conversion (the AKMS workflow does not
          consume them).
        * ``source`` is forced to ``"imported"`` — every batch arriving in
          the AKMS workspace via ``send_to_akms`` is an import from the
          Assistant side. ``nodes`` / ``generation`` / ``round*`` /
          ``zotsums_text`` / ``sources_text`` / ``notes`` all take their
          model defaults.

        The returned :class:`EditableBatch` is a fresh instance — callers
        own it and may mutate without affecting this :class:`SourceBatch`.
        """
        paper_refs = [_source_paper_ref(item) for item in self.sources]
        # The AKMS-side UI surfaces sources in two places: the overview
        # header reads `papers.length`, but the "Sources" tab in
        # BatchesPage renders `sources_text` (a freeform per-line
        # listing). Mirror the paper refs into `sources_text` so the
        # imported batch reads as having sources in both spots; without
        # it the AKMS Sources tab showed "0 sources".
        return EditableBatch(
            id=self.id,
            title=self.title,
            description=self.description,
            status=self.status,
            source="imported",
            created_at=self.created_at,
            updated_at=self.updated_at,
            papers=paper_refs,
            sources_text="\n".join(paper_refs),
            notebook_id=self.notebook_id,
            default_workflow_id=self.default_workflow_id,
            prompt_profile_id=self.prompt_profile_id,
            prompt_overrides=dict(self.prompt_overrides),
        )


def _source_paper_ref(item: BatchSourceItem) -> str:
    """Return a canonical paper-ref string for a :class:`BatchSourceItem`.

    Preference order — first non-empty wins:

    1. ``citekey`` (the user's local catalog identifier)
    2. ``doi`` (URN-stable)
    3. ``openalex_id`` (URN-stable)
    4. ``semantic_scholar_id`` (URN-stable)
    5. ``url`` (best-effort for url-kind sources)
    6. ``local_pdf_path`` / ``external_pdf_url``
    7. ``id`` (the source-item id — always present)

    Determinism: same input always yields the same ref. The AKMS-side
    workflow treats this string opaquely; the Assistant side keeps the
    full record so it can re-resolve if needed.
    """
    for value in (
        item.citekey,
        item.doi,
        item.openalex_id,
        item.semantic_scholar_id,
        item.url,
        item.local_pdf_path,
        item.external_pdf_url,
    ):
        if value:
            return value
    return item.id


__all__ = [
    "SCHEMA_VERSION",
    "BatchSourceItem",
    "SourceBatch",
    "SourceBatchStatus",
    "SourceItemKind",
    "ZoteroMatch",
]
