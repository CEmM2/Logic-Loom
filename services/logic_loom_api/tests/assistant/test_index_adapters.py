"""Tests for the indexable artefact schema + ingestion pipeline.

Covers:
  1. Each adapter produces records that parse cleanly as IndexableRecord.

Additional coverage:
  * content_hash is stable across rerun on identical inputs (per the
    schema's documented contract).
  * Updating the underlying artefact changes the content_hash.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logic_loom_api.assistant.research_index import ResearchIndexService
from logic_loom_api.assistant.research_index.ingest import (
    ADAPTERS,
    ingest_abstract,
    ingest_akms_node,
    ingest_assistant_output,
    ingest_run_metadata,
    ingest_zotero,
    ingest_zsum_card,
    ingest_zsum_digest,
)
from logic_loom_api.assistant.research_index.schema import (
    INDEX_KINDS,
    IndexableRecord,
    compute_content_hash,
)
from logic_loom_api.config import Settings


def _make_settings(
    tmp_path: Path,
    *,
    with_vault: bool = True,
    with_assistant_workspace: bool = True,
    with_akms_workspace: bool = True,
    with_akms_vault: bool = True,
) -> Settings:
    """Build a Settings fixture rooted under ``tmp_path``.

    Each toggle controls whether the corresponding directory tree is
    materialised. Tests that exercise only one adapter use the toggles
    to keep the other adapters' outputs at zero records.
    """
    akms_workspace = tmp_path / "AKMS-Workspace"
    if with_akms_workspace:
        akms_workspace.mkdir(parents=True, exist_ok=True)
    akms_repo = tmp_path / "AKMS-repo"
    if with_akms_vault:
        (akms_repo / "Packages" / "Nodes_Vault").mkdir(parents=True, exist_ok=True)
    assistant_workspace = tmp_path / "Assistant-Workspace"
    if with_assistant_workspace:
        assistant_workspace.mkdir(parents=True, exist_ok=True)
    vault_path = tmp_path / "Vault" if with_vault else None
    if with_vault:
        vault_path.mkdir(parents=True, exist_ok=True)
    return Settings(
        akms_workspace=akms_workspace,
        akms_repo=akms_repo,
        assistant_workspace=assistant_workspace,
        vault_path=vault_path,
    )


def _write_batches(settings: Settings, batches: list[dict]) -> None:
    """Write a ``batches.json`` envelope to the assistant workspace."""
    settings.assistant_batches_path().write_text(
        json.dumps({"schema_version": 1, "batches": batches}, indent=2)
    )


class TestSchema:
    """Tests for :mod:`research_index.schema`."""

    @pytest.mark.unit
    def test_indexable_record_round_trip(self) -> None:
        """Verifies: IndexableRecord round-trips through model_validate."""
        rec = IndexableRecord(
            id="zotero:b1:s1",
            kind="zotero",
            title="A paper",
            body="line1\nline2",
            source_path="/tmp/x.json",
            batch_id="b1",
            content_hash=compute_content_hash(
                kind="zotero", title="A paper", body="line1\nline2"
            ),
        )
        copy = IndexableRecord.model_validate(rec.model_dump())
        assert copy == rec

    @pytest.mark.unit
    def test_compute_content_hash_is_stable(self) -> None:
        """content_hash is deterministic across calls."""
        a = compute_content_hash(kind="zotero", title="T", body="B")
        b = compute_content_hash(kind="zotero", title="T", body="B")
        assert a == b
        assert len(a) == 64  # sha256 hex

    @pytest.mark.unit
    def test_compute_content_hash_changes_on_input_change(self) -> None:
        """Changing any of the three inputs changes the hash."""
        base = compute_content_hash(kind="zotero", title="T", body="B")
        assert base != compute_content_hash(kind="abstract", title="T", body="B")
        assert base != compute_content_hash(kind="zotero", title="T2", body="B")
        assert base != compute_content_hash(kind="zotero", title="T", body="B2")

    @pytest.mark.unit
    def test_with_computed_hash_fills_blank(self) -> None:
        """with_computed_hash populates the hash when blank."""
        rec = IndexableRecord(id="x", kind="zotero", title="T", body="B")
        assert rec.content_hash == ""
        hashed = rec.with_computed_hash()
        assert hashed.content_hash == compute_content_hash(
            kind="zotero", title="T", body="B"
        )

    @pytest.mark.unit
    def test_index_kinds_match_literal(self) -> None:
        """The runtime tuple matches the typing.Literal — guards against
        scaffold-time drift where one is updated without the other."""
        # All seven kinds present
        assert set(INDEX_KINDS) == {
            "zotero",
            "abstract",
            "zsum_card",
            "zsum_digest",
            "assistant_output",
            "akms_node",
            "run_metadata",
        }


class TestAdapterCoverage:
    """One test per kind — each adapter produces parseable records."""

    @pytest.mark.unit
    def test_zotero_adapter(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path, with_vault=False)
        _write_batches(
            s,
            [
                {
                    "id": "b1",
                    "title": "My Batch",
                    "sources": [
                        {
                            "id": "src-1",
                            "kind": "zotero",
                            "title": "Zot Paper",
                            "authors": ["Smith J", "Doe K"],
                            "year": 2024,
                            "doi": "10.1/abc",
                            "citekey": "smith2024",
                        }
                    ],
                }
            ],
        )
        records = list(ingest_zotero(s))
        assert len(records) == 1
        r = records[0]
        assert isinstance(r, IndexableRecord)
        assert r.kind == "zotero"
        assert r.title == "Zot Paper"
        assert r.batch_id == "b1"
        assert "Smith J" in r.body
        assert "10.1/abc" in r.body
        assert r.content_hash, "content_hash must be populated"
        # ID is deterministic
        assert r.id == "zotero:b1:src-1"

    @pytest.mark.unit
    def test_abstract_adapter(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path, with_vault=False)
        cache_dir = s.assistant_workspace_path() / "discovery_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "abc.json").write_text(
            json.dumps(
                {
                    "papers": [
                        {
                            "title": "Discovered Paper",
                            "abstract": "Long abstract text",
                            "authors": ["Lee A"],
                            "year": 2023,
                            "doi": "10.5/xyz",
                        }
                    ]
                }
            )
        )
        records = list(ingest_abstract(s))
        assert len(records) == 1
        r = records[0]
        assert r.kind == "abstract"
        assert r.title == "Discovered Paper"
        assert "Long abstract text" in r.body
        assert r.id == "abstract:10.5/xyz"

    @pytest.mark.unit
    def test_zsum_card_adapter(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path)
        papers_dir = s.vault_path / "Papers"  # type: ignore[union-attr]
        papers_dir.mkdir(parents=True, exist_ok=True)
        (papers_dir / "@smith2024.md").write_text("# Smith 2024\n\nbody text")
        records = list(ingest_zsum_card(s))
        assert len(records) == 1
        r = records[0]
        assert r.kind == "zsum_card"
        assert r.title == "@smith2024"
        assert "body text" in r.body

    @pytest.mark.unit
    def test_zsum_digest_adapter(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path)
        col_dir = s.vault_path / "Collections"  # type: ignore[union-attr]
        col_dir.mkdir(parents=True, exist_ok=True)
        (col_dir / "Plasticity.md").write_text("# Plasticity\n\ndigest body")
        records = list(ingest_zsum_digest(s))
        assert len(records) == 1
        r = records[0]
        assert r.kind == "zsum_digest"
        assert "digest body" in r.body

    @pytest.mark.unit
    def test_assistant_output_adapter(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path, with_vault=False)
        run_dir = (
            s.akms_workspace
            / "outputs"
            / "b1"
            / "assistant.literature_survey"
            / "run_2026-05-15_1000_demo"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "output.md").write_text("# Survey Output\n\nFindings")
        (run_dir / "metadata.json").write_text("{}")
        records = list(ingest_assistant_output(s))
        assert len(records) == 1
        r = records[0]
        assert r.kind == "assistant_output"
        assert "Findings" in r.body
        assert r.batch_id == "b1"
        assert r.workflow_id == "assistant.literature_survey"
        assert r.run_id == "run_2026-05-15_1000_demo"

    @pytest.mark.unit
    def test_assistant_output_skips_non_assistant_workflow(
        self, tmp_path: Path
    ) -> None:
        s = _make_settings(tmp_path, with_vault=False)
        run_dir = (
            s.akms_workspace
            / "outputs"
            / "b1"
            / "akms.node_extraction"
            / "run_2026-05-15_1100_other"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "output.md").write_text("# AKMS run\n")
        records = list(ingest_assistant_output(s))
        assert records == []

    @pytest.mark.unit
    def test_akms_node_adapter(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path, with_vault=False)
        vault = s.effective_akms_global_vault()
        (vault / "node_foo.md").write_text("---\nid: foo\n---\nbody foo")
        records = list(ingest_akms_node(s))
        assert len(records) == 1
        r = records[0]
        assert r.kind == "akms_node"
        assert "body foo" in r.body

    @pytest.mark.unit
    def test_run_metadata_adapter(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path, with_vault=False)
        run_dir = (
            s.akms_workspace
            / "outputs"
            / "b1"
            / "assistant.literature_survey"
            / "run_2026-05-15_1200_x"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        meta_payload = {
            "id": "run_2026-05-15_1200_x",
            "started_at": "2026-05-15T12:00:00",
            "workflow_id": "assistant.literature_survey",
        }
        (run_dir / "metadata.json").write_text(json.dumps(meta_payload))
        records = list(ingest_run_metadata(s))
        assert len(records) == 1
        r = records[0]
        assert r.kind == "run_metadata"
        assert "assistant.literature_survey" in r.body
        assert r.run_id == "run_2026-05-15_1200_x"

    @pytest.mark.unit
    def test_dispatch_table_covers_every_kind(self) -> None:
        """ADAPTERS keys match the closed-set IndexKind literal."""
        assert set(ADAPTERS.keys()) == set(INDEX_KINDS)


class TestContentHashStability:
    """Corollary: content_hash is the change-signal driver."""

    @pytest.mark.unit
    def test_content_hash_stable_across_rerun(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path, with_vault=False)
        _write_batches(
            s,
            [
                {
                    "id": "b1",
                    "title": "T",
                    "sources": [
                        {
                            "id": "s1",
                            "kind": "zotero",
                            "title": "Paper",
                            "doi": "10.1/x",
                        }
                    ],
                }
            ],
        )
        records_a = list(ingest_zotero(s))
        records_b = list(ingest_zotero(s))
        assert len(records_a) == len(records_b) == 1
        assert records_a[0].content_hash == records_b[0].content_hash

    @pytest.mark.unit
    def test_content_hash_changes_on_artefact_update(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path, with_vault=False)
        # First write
        _write_batches(
            s,
            [
                {
                    "id": "b1",
                    "title": "T",
                    "sources": [
                        {
                            "id": "s1",
                            "kind": "zotero",
                            "title": "Old Title",
                            "doi": "10.1/x",
                        }
                    ],
                }
            ],
        )
        before = list(ingest_zotero(s))[0].content_hash
        # Update the title (which feeds the hash via _zotero_body_for)
        _write_batches(
            s,
            [
                {
                    "id": "b1",
                    "title": "T",
                    "sources": [
                        {
                            "id": "s1",
                            "kind": "zotero",
                            "title": "New Title",
                            "doi": "10.1/x",
                        }
                    ],
                }
            ],
        )
        after = list(ingest_zotero(s))[0].content_hash
        assert before != after


class TestServiceFacade:
    """The stub :class:`ResearchIndexService` factory still works."""

    @pytest.mark.unit
    def test_research_index_service_constructs(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path)
        svc = ResearchIndexService(s)
        assert svc is not None
