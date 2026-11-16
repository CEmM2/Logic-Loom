"""Tests for the research index lifecycle (incremental + full rebuild).

Covers:
  1. Creating a batch then searching its title finds it.
  2. Deleting a batch removes the record.

Additional coverage:
  * POST /api/assistant/index/rebuild restores records after manual wipe.
  * GET  /api/assistant/index/status returns the closed-set per-kind
    counts + last-rebuild timestamp.
  * Debounce: 10 rapid upserts collapse into one indexer pass within
    a 500ms window.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from logic_loom_api.assistant.research_index import (
    IndexLifecycle,
    ResearchIndexService,
)
from logic_loom_api.assistant.research_index.ingest import ingest_zotero
from logic_loom_api.assistant.research_index.schema import (
    INDEX_KINDS,
    IndexableRecord,
    compute_content_hash,
)
from logic_loom_api.config import Settings
from logic_loom_api.deps import get_settings
from logic_loom_api.routes import assistant_index as assistant_index_routes


def _make_settings(tmp_path: Path, *, with_batches: bool = True) -> Settings:
    """Build a Settings rooted under tmp_path with the canonical layout."""
    akms_workspace = tmp_path / "AKMS-Workspace"
    akms_workspace.mkdir(parents=True, exist_ok=True)
    akms_repo = tmp_path / "AKMS-repo"
    (akms_repo / "Packages" / "Nodes_Vault").mkdir(parents=True, exist_ok=True)
    assistant_workspace = tmp_path / "Assistant-Workspace"
    assistant_workspace.mkdir(parents=True, exist_ok=True)
    if with_batches:
        # Seed empty batches.json so the zotero adapter is well-defined.
        (assistant_workspace / "batches.json").write_text(
            json.dumps({"schema_version": 1, "batches": []}, indent=2)
        )
    return Settings(
        akms_workspace=akms_workspace,
        akms_repo=akms_repo,
        assistant_workspace=assistant_workspace,
        vault_path=tmp_path / "Vault",
    )


def _seed_batch(
    settings: Settings,
    *,
    batch_id: str = "b1",
    title: str = "Plasticity Survey",
    source_title: str = "Finite strain plasticity paper",
) -> None:
    """Write a single batch with one zotero source to batches.json."""
    payload = {
        "schema_version": 1,
        "batches": [
            {
                "id": batch_id,
                "title": title,
                "sources": [
                    {
                        "id": "s1",
                        "kind": "zotero",
                        "title": source_title,
                        "doi": "10.1/x",
                    }
                ],
            }
        ],
    }
    settings.assistant_batches_path().write_text(json.dumps(payload, indent=2))


@pytest.fixture
def lifecycle(tmp_path: Path) -> IndexLifecycle:
    """Build an IndexLifecycle with a tight debounce window for tests."""
    s = _make_settings(tmp_path)
    lc = IndexLifecycle(s, debounce_ms=50)
    yield lc
    lc.close()


class TestRebuild:
    """Full-rebuild walks every workspace artefact."""

    @pytest.mark.integration
    def test_create_batch_then_search_finds_title(
        self, tmp_path: Path
    ) -> None:
        """Verifies: creating a batch + rebuilding makes its title searchable."""
        s = _make_settings(tmp_path)
        _seed_batch(
            s,
            title="Plasticity Survey",
            source_title="Finite strain plasticity paper",
        )
        svc = ResearchIndexService(s)
        counts = svc.rebuild()
        assert counts["zotero"] == 1
        results = svc.search("plasticity")
        assert len(results) == 1
        assert "plasticity" in results[0].body.lower()

    @pytest.mark.integration
    def test_delete_batch_removes_record(self, tmp_path: Path) -> None:
        """Verifies: removing the source from batches.json + rebuilding
        evicts the record."""
        s = _make_settings(tmp_path)
        _seed_batch(s, source_title="Finite strain plasticity paper")
        svc = ResearchIndexService(s)
        svc.rebuild()
        assert len(svc.search("plasticity")) == 1
        # Wipe the batches file.
        s.assistant_batches_path().write_text(
            json.dumps({"schema_version": 1, "batches": []}, indent=2)
        )
        svc.rebuild()
        assert svc.search("plasticity") == []

    @pytest.mark.integration
    def test_rebuild_restores_after_wipe(self, tmp_path: Path) -> None:
        """POST /index/rebuild walks every artefact and reindexes."""
        s = _make_settings(tmp_path)
        _seed_batch(s)
        svc = ResearchIndexService(s)
        svc.rebuild()
        assert svc.status()["counts"]["zotero"] == 1
        # Manual wipe of the fts.sqlite — simulates corruption recovery.
        idx_path = s.assistant_workspace_path() / "index" / "fts.sqlite"
        # Use clear() rather than os.unlink so the FtsIndex stays
        # connected; the rebuild() call is the real test.
        svc._get_lifecycle()._fts.clear()  # type: ignore[attr-defined]
        assert svc.status()["counts"]["zotero"] == 0
        svc.rebuild()
        assert svc.status()["counts"]["zotero"] == 1
        # Sanity: the on-disk file exists (FtsIndex created it).
        assert idx_path.exists()


class TestStatus:
    """status() returns the closed-set counts + last-rebuild timestamp."""

    @pytest.mark.integration
    def test_status_keys_every_closed_set_kind(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path)
        svc = ResearchIndexService(s)
        st = svc.status()
        assert set(st["counts"].keys()) == set(INDEX_KINDS)
        # Every kind starts at zero.
        assert all(v == 0 for v in st["counts"].values())
        assert st["total"] == 0
        assert st["last_rebuild_at"] is None

    @pytest.mark.integration
    def test_status_records_last_rebuild_at(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path)
        _seed_batch(s)
        svc = ResearchIndexService(s)
        svc.rebuild()
        st = svc.status()
        assert st["last_rebuild_at"] is not None
        assert st["total"] >= 1


class TestDebounce:
    """500ms debounce window collapses bulk enqueues."""

    @pytest.mark.integration
    def test_rapid_upserts_collapse_into_one_flush(
        self, lifecycle: IndexLifecycle
    ) -> None:
        """10 rapid upserts within the debounce window land in one flush."""
        for i in range(10):
            rec = IndexableRecord(
                id=f"rec-{i}",
                kind="zotero",
                title=f"T{i}",
                body=f"alpha{i}",
                content_hash=compute_content_hash(
                    kind="zotero", title=f"T{i}", body=f"alpha{i}"
                ),
            )
            lifecycle.on_change(rec)
        # Buffer holds all 10 — no flush yet.
        assert lifecycle.pending_size == 10
        assert lifecycle.flush_count == 0
        # Force flush rather than waiting on the timer.
        upserts, deletes = lifecycle.flush()
        assert upserts == 10
        assert deletes == 0
        assert lifecycle.flush_count == 1
        # Buffer drained.
        assert lifecycle.pending_size == 0

    @pytest.mark.integration
    def test_timer_fires_after_debounce_window(
        self, lifecycle: IndexLifecycle
    ) -> None:
        """The timer auto-fires after debounce_ms; the buffer drains."""
        rec = IndexableRecord(
            id="rec-1",
            kind="zotero",
            title="T",
            body="alpha",
            content_hash=compute_content_hash(
                kind="zotero", title="T", body="alpha"
            ),
        )
        lifecycle.on_change(rec)
        # Poll rather than sleeping a fixed 0.25s past the fixture's 50ms
        # window: a loaded CI runner can delay the timer thread well beyond
        # any constant we pick, which made this fail intermittently on
        # whichever platform happened to be busiest. The generous ceiling
        # still fails fast if the timer never fires at all.
        deadline = time.monotonic() + 10.0
        while lifecycle.flush_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert lifecycle.flush_count >= 1
        assert lifecycle.pending_size == 0

    @pytest.mark.integration
    def test_on_delete_in_same_window_replaces_on_change(
        self, lifecycle: IndexLifecycle
    ) -> None:
        """Same record_id: latest enqueue wins (the delete sentinel)."""
        rec = IndexableRecord(
            id="rec-1",
            kind="zotero",
            title="T",
            body="alpha",
            content_hash=compute_content_hash(
                kind="zotero", title="T", body="alpha"
            ),
        )
        lifecycle.on_change(rec)
        lifecycle.on_delete("rec-1")
        assert lifecycle.pending_size == 1
        upserts, deletes = lifecycle.flush()
        # The record was never on-disk; the delete returns False so the
        # deletes counter stays at zero. Important: the on_change is NOT
        # applied because the delete sentinel replaced it in the buffer.
        assert upserts == 0
        assert deletes == 0


class TestRoutes:
    """Mounted /api/assistant/index/* routes work end-to-end."""

    def _build_client(self, settings: Settings) -> TestClient:
        """Build a minimal FastAPI app with just the index routes mounted.

        The dependency override on get_settings injects the test
        settings; the global lifecycle cache is reset between tests
        via reset_index_lifecycle().
        """
        app = FastAPI()
        app.include_router(
            assistant_index_routes.router,
            prefix="/api/assistant/index",
        )
        app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app)

    @pytest.fixture(autouse=True)
    def _reset_lifecycle_cache(self):
        """Clear the singleton cache between tests."""
        assistant_index_routes.reset_index_lifecycle()
        yield
        assistant_index_routes.reset_index_lifecycle()

    @pytest.mark.integration
    def test_status_route_empty_workspace(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path)
        client = self._build_client(s)
        res = client.get("/api/assistant/index/status")
        assert res.status_code == 200
        body = res.json()
        assert set(body["counts"].keys()) == set(INDEX_KINDS)
        assert body["total"] == 0
        assert body["last_rebuild_at"] is None

    @pytest.mark.integration
    def test_rebuild_route_walks_seeded_workspace(self, tmp_path: Path) -> None:
        s = _make_settings(tmp_path)
        _seed_batch(s, source_title="Finite strain plasticity")
        client = self._build_client(s)
        res = client.post("/api/assistant/index/rebuild")
        assert res.status_code == 200
        body = res.json()
        assert body["counts"]["zotero"] == 1
        assert body["total"] >= 1
        assert body["last_rebuild_at"] is not None

        # Status reflects the post-rebuild state.
        status_res = client.get("/api/assistant/index/status")
        assert status_res.status_code == 200
        st = status_res.json()
        assert st["counts"]["zotero"] == 1
        assert st["last_rebuild_at"] is not None


class TestIngestionFromBatchStore:
    """The zotero adapter reads from the same batches.json BatchStore writes."""

    @pytest.mark.integration
    def test_zotero_adapter_consumes_batchstore_output(
        self, tmp_path: Path
    ) -> None:
        s = _make_settings(tmp_path)
        _seed_batch(
            s,
            batch_id="b-real",
            title="Real Batch",
            source_title="Adapter Integration Paper",
        )
        records = list(ingest_zotero(s))
        assert len(records) == 1
        assert records[0].batch_id == "b-real"
        assert "Adapter Integration Paper" in records[0].title
