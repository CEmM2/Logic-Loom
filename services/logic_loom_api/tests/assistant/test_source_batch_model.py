"""Tests for the SourceBatch model and BatchSourceItem.

Covers:
  1. Round-trip JSON: SourceBatch.model_validate(SourceBatch(...).model_dump())
     is the identity transformation.
  2. to_editable_batch() preserves id, title, sources (mapped to AKMS papers),
     and prompt_* fields.

Plus validation: rejects unknown `kind` values on BatchSourceItem (closed
literal — adding a new kind is a deliberate design decision).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from logic_loom_api.assistant.models import (
    BatchSourceItem,
    SourceBatch,
    SourceItemKind,
    ZoteroMatch,
    _source_paper_ref,
)
from logic_loom_api.batches.models import EditableBatch


def _make_item(kind: SourceItemKind, idx: int) -> BatchSourceItem:
    """Construct a BatchSourceItem of the requested kind with kind-appropriate fields populated."""
    base: dict[str, object] = {
        "id": f"src-{kind}-{idx}",
        "kind": kind,
        "title": f"{kind.title()} Paper #{idx}",
        "authors": [f"Author {idx}A", f"Author {idx}B"],
        "year": 2024,
    }
    if kind == "zotero":
        base["citekey"] = f"author{idx}2024"
        base["zotero_match"] = ZoteroMatch(citekey=f"author{idx}2024")
    elif kind == "discovered":
        base["doi"] = f"10.1234/discovered.{idx}"
        base["openalex_id"] = f"W{idx:09d}"
    elif kind == "external_pdf":
        base["external_pdf_url"] = f"https://example.com/papers/{idx}.pdf"
    elif kind == "url":
        base["url"] = f"https://example.com/article/{idx}"
    elif kind == "manual":
        base["local_pdf_path"] = f"/tmp/manual-{idx}.pdf"
    return BatchSourceItem(**base)


class TestSourceBatchModel:
    """Tests for the SourceBatch model and BatchSourceItem."""

    @pytest.mark.unit
    def test_round_trip_for_each_kind_value(self) -> None:
        """Verifies: SourceBatch.model_validate(SourceBatch(...).model_dump())
        is identity for batches containing every allowed source kind.

        Requirement: Round-trip JSON.
        """
        kinds: tuple[SourceItemKind, ...] = (
            "zotero",
            "discovered",
            "external_pdf",
            "url",
            "manual",
        )
        sources = [_make_item(kind, i) for i, kind in enumerate(kinds, start=1)]
        original = SourceBatch(
            id="batch-roundtrip",
            title="Round-trip batch",
            description="covers every source kind",
            status="active",
            tags=["roundtrip", "p2-1"],
            purpose="unit test",
            sources=sources,
            notebook_id="notebook-xyz",
            default_workflow_id="wf-1",
            prompt_profile_id="prof-1",
            prompt_overrides={"system": "Hi"},
            akms_node_plan=[{"node_id": "n1", "title": "Node 1"}],
            created_at="2026-05-15T00:00:00+00:00",
            updated_at="2026-05-15T00:00:00+00:00",
        )

        dumped = original.model_dump()
        revived = SourceBatch.model_validate(dumped)

        assert revived.model_dump() == dumped
        assert revived == original
        # Each kind survives the round-trip.
        kinds_seen = [item.kind for item in revived.sources]
        assert kinds_seen == list(kinds)

    @pytest.mark.unit
    def test_to_editable_batch_equivalence(self) -> None:
        """Verifies: to_editable_batch() on a SourceBatch returns an
        EditableBatch whose id, title, papers (derived from sources),
        prompt_profile_id and prompt_overrides match.

        Requirement: to_editable_batch() preserves id, title, sources, prompt_* fields.
        """
        sources = [
            BatchSourceItem(
                id="src-1",
                kind="zotero",
                title="Zotero paper",
                citekey="smith2024",
                doi="10.1234/zot.1",
            ),
            BatchSourceItem(
                id="src-2",
                kind="discovered",
                title="Discovered paper",
                openalex_id="W123456789",
            ),
            BatchSourceItem(
                id="src-3",
                kind="url",
                title="URL source",
                url="https://example.com/abc",
            ),
        ]
        batch = SourceBatch(
            id="batch-eq",
            title="Equivalence batch",
            description="conversion fixture",
            status="active",
            sources=sources,
            notebook_id="nb-eq",
            default_workflow_id="wf-eq",
            prompt_profile_id="prof-eq",
            prompt_overrides={"variant": "experiment"},
            created_at="2026-05-15T01:00:00+00:00",
            updated_at="2026-05-15T01:00:00+00:00",
        )

        editable = batch.to_editable_batch()

        assert isinstance(editable, EditableBatch)
        assert editable.id == batch.id
        assert editable.title == batch.title
        assert editable.description == batch.description
        assert editable.status == batch.status
        assert editable.source == "imported"
        assert editable.created_at == batch.created_at
        assert editable.updated_at == batch.updated_at
        assert editable.notebook_id == batch.notebook_id
        assert editable.default_workflow_id == batch.default_workflow_id
        assert editable.prompt_profile_id == batch.prompt_profile_id
        assert editable.prompt_overrides == batch.prompt_overrides
        # papers maps via _source_paper_ref preference order.
        assert editable.papers == [
            "smith2024",  # citekey wins
            "W123456789",  # openalex id (no citekey/doi)
            "https://example.com/abc",  # url
        ]
        # T2.5: sources_text must mirror papers so the AKMS Sources tab
        # in BatchesPage (which renders the freeform text field, not the
        # papers list) shows the imported references instead of being
        # blank. Format is one ref per line.
        assert editable.sources_text == "\n".join(editable.papers)
        # AKMS-side EditableBatch round-trips through its own schema cleanly.
        assert EditableBatch.model_validate(editable.model_dump()) == editable

    @pytest.mark.unit
    def test_rejects_unknown_kind(self) -> None:
        """Verifies: BatchSourceItem(kind='bogus') raises ValidationError.

        Requirement: kind is a closed Literal.
        """
        with pytest.raises(ValidationError):
            BatchSourceItem(
                id="src-bad",
                kind="bogus",  # type: ignore[arg-type]
                title="invalid",
            )

    @pytest.mark.unit
    def test_source_paper_ref_preference_order(self) -> None:
        """Verifies: _source_paper_ref picks identifiers in the documented order.

        Requirement: helper for to_editable_batch — ensures conversion contract is
        deterministic across kinds.
        """
        item_citekey = BatchSourceItem(
            id="x",
            kind="zotero",
            title="t",
            citekey="ck-1",
            doi="10/doi",
        )
        assert _source_paper_ref(item_citekey) == "ck-1"

        item_doi = BatchSourceItem(
            id="x",
            kind="discovered",
            title="t",
            doi="10.0/abc",
            openalex_id="W1",
        )
        assert _source_paper_ref(item_doi) == "10.0/abc"

        item_url = BatchSourceItem(
            id="x",
            kind="url",
            title="t",
            url="https://example.com",
        )
        assert _source_paper_ref(item_url) == "https://example.com"

        # When nothing is set, fall back to the item id.
        item_bare = BatchSourceItem(id="bare-id", kind="manual", title="t")
        assert _source_paper_ref(item_bare) == "bare-id"
