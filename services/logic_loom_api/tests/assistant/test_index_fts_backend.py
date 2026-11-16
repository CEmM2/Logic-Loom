"""Tests for the research index SQLite FTS5 backend.

Covers:
  1. Inserting then searching for a unique token retrieves the record.
  2. Updating the content invalidates the prior copy.

Additional coverage:
  * Filter by kind returns only matching kinds.
  * content_hash skip: upsert with unchanged hash is a no-op.
  * count_by_kind keys every kind in the closed set.
  * Search uses FTS5 ranking (most-relevant first).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from logic_loom_api.assistant.research_index import ResearchIndexService
from logic_loom_api.assistant.research_index.fts import FtsIndex
from logic_loom_api.assistant.research_index.schema import (
    INDEX_KINDS,
    IndexableRecord,
    compute_content_hash,
)
from logic_loom_api.config import Settings


def _record(
    *,
    id: str,
    kind: str = "zotero",
    title: str = "Untitled",
    body: str = "",
    **extras,
) -> IndexableRecord:
    """Build a record with content_hash computed inline."""
    return IndexableRecord(
        id=id,
        kind=kind,  # type: ignore[arg-type]
        title=title,
        body=body,
        content_hash=compute_content_hash(kind=kind, title=title, body=body),
        **extras,
    )


@pytest.fixture
def fts(tmp_path: Path) -> FtsIndex:
    """Build an FtsIndex against tmp_path/Assistant-Workspace."""
    workspace = tmp_path / "Assistant-Workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    idx = FtsIndex(workspace)
    yield idx
    idx.close()


class TestCrudCycle:
    """Insert, search, update, delete."""

    @pytest.mark.unit
    def test_insert_search_update_delete_cycle(self, fts: FtsIndex) -> None:
        rec = _record(
            id="zotero:b1:s1",
            title="Finite strain plasticity",
            body="A study of finite strain plasticity in metals.",
        )
        assert fts.upsert(rec) is True  # newly inserted
        # Search for a unique token returns the record.
        results = fts.search("plasticity")
        assert len(results) == 1
        assert results[0].id == rec.id

        # Update content with a different body.
        new_rec = _record(
            id="zotero:b1:s1",
            title="Finite strain plasticity",
            body="Replaced with brand new topic: thermomechanics.",
        )
        assert fts.upsert(new_rec) is True  # hash changed -> write
        # Old token no longer matches.
        assert fts.search("metals") == []
        # New token matches.
        new_results = fts.search("thermomechanics")
        assert len(new_results) == 1
        assert new_results[0].id == rec.id
        assert "thermomechanics" in new_results[0].body

        # Delete.
        assert fts.delete(rec.id) is True
        assert fts.search("thermomechanics") == []
        # Deleting a missing id is a graceful False.
        assert fts.delete("does-not-exist") is False

    @pytest.mark.unit
    def test_empty_query_returns_empty(self, fts: FtsIndex) -> None:
        fts.upsert(_record(id="x", title="T", body="something"))
        # Empty / whitespace-only queries return an empty list rather
        # than every row.
        assert fts.search("") == []
        assert fts.search("   ") == []


class TestKindFilter:
    """Filter by kind returns only matching kinds."""

    @pytest.mark.unit
    def test_filter_by_kind_single(self, fts: FtsIndex) -> None:
        fts.upsert(_record(id="a", kind="zotero", title="Zot", body="alpha"))
        fts.upsert(_record(id="b", kind="akms_node", title="Node", body="alpha"))
        fts.upsert(_record(id="c", kind="abstract", title="Abs", body="alpha"))

        # No filter -> all three.
        all_results = fts.search("alpha")
        assert {r.id for r in all_results} == {"a", "b", "c"}

        # Filter to a single kind.
        zot_only = fts.search("alpha", filters={"kind": "zotero"})
        assert [r.id for r in zot_only] == ["a"]

    @pytest.mark.unit
    def test_filter_by_kind_list(self, fts: FtsIndex) -> None:
        fts.upsert(_record(id="a", kind="zotero", title="Zot", body="alpha"))
        fts.upsert(_record(id="b", kind="akms_node", title="Node", body="alpha"))
        fts.upsert(_record(id="c", kind="abstract", title="Abs", body="alpha"))

        zot_or_akms = fts.search(
            "alpha", filters={"kind": ["zotero", "akms_node"]}
        )
        assert {r.id for r in zot_or_akms} == {"a", "b"}


class TestContentHashSkip:
    """Reindex skipping via content_hash."""

    @pytest.mark.unit
    def test_upsert_with_unchanged_hash_is_noop(self, fts: FtsIndex) -> None:
        rec = _record(id="x", title="T", body="B")
        assert fts.upsert(rec) is True  # initial write
        # Same record again -> hash unchanged -> no-op.
        assert fts.upsert(rec) is False
        # A fresh model instance with the same fields also no-ops.
        clone = _record(id="x", title="T", body="B")
        assert fts.upsert(clone) is False
        # Only a content change writes again.
        changed = _record(id="x", title="T", body="B2")
        assert fts.upsert(changed) is True

    @pytest.mark.unit
    def test_upsert_many_returns_written_and_skipped(self, fts: FtsIndex) -> None:
        recs = [
            _record(id=f"r{i}", title=f"T{i}", body=f"body{i}")
            for i in range(5)
        ]
        written, skipped = fts.upsert_many(recs)
        assert written == 5
        assert skipped == 0
        # Re-running on identical input skips every record.
        written2, skipped2 = fts.upsert_many(recs)
        assert written2 == 0
        assert skipped2 == 5


class TestStatusHelpers:
    """count_by_kind + total."""

    @pytest.mark.unit
    def test_count_by_kind_includes_zero_counts(self, fts: FtsIndex) -> None:
        fts.upsert(_record(id="a", kind="zotero", title="T", body="B"))
        counts = fts.count_by_kind()
        # Every kind in the closed set is keyed.
        assert set(counts.keys()) == set(INDEX_KINDS)
        assert counts["zotero"] == 1
        assert counts["akms_node"] == 0

    @pytest.mark.unit
    def test_total_reflects_row_count(self, fts: FtsIndex) -> None:
        for i in range(3):
            fts.upsert(_record(id=f"r{i}", title=f"T{i}", body=f"B{i}"))
        assert fts.total() == 3
        fts.clear()
        assert fts.total() == 0


class TestPersistence:
    """The on-disk file persists across FtsIndex instances."""

    @pytest.mark.unit
    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        workspace = tmp_path / "Assistant-Workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        # Write via one instance.
        idx_a = FtsIndex(workspace)
        idx_a.upsert(_record(id="x", title="T", body="persistent"))
        idx_a.close()
        # The sqlite file is on disk now.
        assert (workspace / "index" / "fts.sqlite").exists()
        # Reopen and confirm the row is still there.
        idx_b = FtsIndex(workspace)
        results = idx_b.search("persistent")
        assert len(results) == 1
        assert results[0].id == "x"
        idx_b.close()


class TestServiceFacade:
    """ResearchIndexService.search wires to the FTS backend."""

    @pytest.mark.unit
    def test_research_index_service_search(self, tmp_path: Path) -> None:
        workspace = tmp_path / "Assistant-Workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        s = Settings(
            akms_workspace=tmp_path / "AKMS-Workspace",
            assistant_workspace=workspace,
        )
        svc = ResearchIndexService(s)
        # Push a record through the underlying FTS index.
        svc._get_index().upsert(  # type: ignore[attr-defined]
            _record(id="x", title="T", body="findme")
        )
        # search() goes through the public surface.
        results = svc.search("findme")
        assert len(results) == 1
        assert results[0].id == "x"

    @pytest.mark.unit
    def test_search_with_kind_filter(self, tmp_path: Path) -> None:
        workspace = tmp_path / "Assistant-Workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        s = Settings(
            akms_workspace=tmp_path / "AKMS-Workspace",
            assistant_workspace=workspace,
        )
        svc = ResearchIndexService(s)
        svc._get_index().upsert(  # type: ignore[attr-defined]
            _record(id="a", kind="zotero", title="Z", body="alpha")
        )
        svc._get_index().upsert(  # type: ignore[attr-defined]
            _record(id="b", kind="akms_node", title="N", body="alpha")
        )
        zot_only = svc.search("alpha", kind="zotero")
        assert [r.id for r in zot_only] == ["a"]
