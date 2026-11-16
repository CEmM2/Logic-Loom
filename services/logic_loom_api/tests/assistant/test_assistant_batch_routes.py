"""Tests for the Assistant batch routes.

Covers:
  1. Each endpoint at /api/assistant/batches/* returns the documented shape
     against TestClient.
  2. send-to-akms returns {akms_batch_id, akms_workspace_path}.

Tests use the FastAPI TestClient against the real app so the mount,
dependency wiring, and lifespan hooks all exercise. Tmp paths are
injected via LOGIC_LOOM_* env vars (same pattern as
test_assistant_workspace_bootstrap.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api import deps
from logic_loom_api.batches.store import BatchStore


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with tmp-injected workspaces.

    Uses the production env-precedence chain: LOGIC_LOOM_* env vars are
    consumed by pydantic-settings on Settings.load(). lru_cache on
    deps.get_settings is busted before and after so each test sees a
    fresh Settings.
    """
    akms_ws = tmp_path / "AKMS-Workspace"
    assistant_ws = tmp_path / "AssistantOnly"
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(akms_ws))
    monkeypatch.setenv("LOGIC_LOOM_ASSISTANT_WORKSPACE", str(assistant_ws))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    # Empty config file so user TOML doesn't override env.
    cfg = tmp_path / "logic-loom.toml"
    cfg.write_text("")
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(cfg))
    deps.get_settings.cache_clear()

    from logic_loom_api.main import api

    with TestClient(api) as tc:
        yield tc

    deps.get_settings.cache_clear()


class TestAssistantBatchRoutes:
    """Tests for the /api/assistant/batches/* routes."""

    @pytest.mark.integration
    def test_crud_flow_end_to_end(self, client: TestClient) -> None:
        """Verifies: TestClient flow exercising POST /api/assistant/batches,
        POST /{id}/sources/add, POST /{id}/sources/reorder,
        POST /{id}/duplicate, POST /{id}/archive, and GET / shapes.

        Requirement: Each endpoint returns documented shape against TestClient.
        """
        # GET empty list.
        r = client.get("/api/assistant/batches")
        assert r.status_code == 200
        assert r.json() == {"batches": []}

        # POST create with title.
        r = client.post(
            "/api/assistant/batches",
            json={"title": "Routes Batch", "description": "via TestClient"},
        )
        assert r.status_code == 200
        created = r.json()
        assert created["id"] == "routes-batch"
        assert created["title"] == "Routes Batch"
        assert created["status"] == "active"
        assert created["sources"] == []
        batch_id = created["id"]

        # GET single batch.
        r = client.get(f"/api/assistant/batches/{batch_id}")
        assert r.status_code == 200
        assert r.json()["id"] == batch_id

        # PATCH — update description + tags.
        r = client.patch(
            f"/api/assistant/batches/{batch_id}",
            json={"description": "patched", "tags": ["alpha", "beta"]},
        )
        assert r.status_code == 200
        patched = r.json()
        assert patched["description"] == "patched"
        assert patched["tags"] == ["alpha", "beta"]

        # POST sources/add.
        r = client.post(
            f"/api/assistant/batches/{batch_id}/sources/add",
            json={
                "sources": [
                    {"id": "a", "kind": "manual", "title": "A"},
                    {"id": "b", "kind": "url", "title": "B", "url": "https://x/b"},
                ]
            },
        )
        assert r.status_code == 200
        with_src = r.json()
        assert [s["id"] for s in with_src["sources"]] == ["a", "b"]

        # POST sources/reorder.
        r = client.post(
            f"/api/assistant/batches/{batch_id}/sources/reorder",
            json={"ordering": ["b", "a"]},
        )
        assert r.status_code == 200
        assert [s["id"] for s in r.json()["sources"]] == ["b", "a"]

        # POST sources/remove.
        r = client.post(
            f"/api/assistant/batches/{batch_id}/sources/remove",
            json={"ids": ["a"]},
        )
        assert r.status_code == 200
        assert [s["id"] for s in r.json()["sources"]] == ["b"]

        # POST duplicate.
        r = client.post(f"/api/assistant/batches/{batch_id}/duplicate")
        assert r.status_code == 200
        clone = r.json()
        assert clone["id"] != batch_id
        assert clone["title"] == "Routes Batch (copy)"
        assert clone["status"] == "draft"

        # POST archive.
        r = client.post(f"/api/assistant/batches/{batch_id}/archive")
        assert r.status_code == 200
        assert r.json()["status"] == "archived"

        # GET list now has both batches.
        r = client.get("/api/assistant/batches")
        assert r.status_code == 200
        ids = [b["id"] for b in r.json()["batches"]]
        assert batch_id in ids
        assert clone["id"] in ids

        # 404 path.
        r = client.get("/api/assistant/batches/missing")
        assert r.status_code == 404

    @pytest.mark.integration
    def test_send_to_akms_returns_paired_payload(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verifies: POST /api/assistant/batches/{id}/send-to-akms returns
        {akms_batch_id: str, akms_workspace_path: str} and the AKMS
        BatchStore can load the new batch by id.

        Requirement: send-to-akms returns paired payload.
        """
        r = client.post(
            "/api/assistant/batches",
            json={
                "title": "Send Me",
                "description": "cross-mode",
                "sources": [
                    {
                        "id": "s1",
                        "kind": "zotero",
                        "title": "Cite One",
                        "citekey": "cite1",
                    }
                ],
                "prompt_profile_id": "prof-handoff",
                "prompt_overrides": {"system": "go"},
            },
        )
        assert r.status_code == 200
        batch_id = r.json()["id"]

        r = client.post(f"/api/assistant/batches/{batch_id}/send-to-akms")
        assert r.status_code == 200
        payload = r.json()
        assert set(payload.keys()) == {"akms_batch_id", "akms_workspace_path"}
        assert isinstance(payload["akms_batch_id"], str)
        assert payload["akms_batch_id"]  # non-empty
        akms_path = Path(payload["akms_workspace_path"])
        assert akms_path == (tmp_path / "AKMS-Workspace").resolve() or akms_path == tmp_path / "AKMS-Workspace"

        # The AKMS BatchStore can read the new batch.
        store = BatchStore.load(akms_path)
        editable = store.get(payload["akms_batch_id"])
        assert editable is not None
        assert editable.title == "Send Me"
        assert editable.source == "imported"
        assert editable.papers == ["cite1"]
        assert editable.prompt_profile_id == "prof-handoff"

        # send-to-akms missing batch → 404.
        r = client.post("/api/assistant/batches/nonexistent/send-to-akms")
        assert r.status_code == 404

    @pytest.mark.integration
    def test_reorder_rejects_partial_ordering(self, client: TestClient) -> None:
        """reorder body that is not a permutation → 400."""
        r = client.post(
            "/api/assistant/batches",
            json={
                "title": "Reorder Bad",
                "sources": [
                    {"id": "a", "kind": "manual", "title": "A"},
                    {"id": "b", "kind": "manual", "title": "B"},
                ],
            },
        )
        assert r.status_code == 200
        batch_id = r.json()["id"]
        r = client.post(
            f"/api/assistant/batches/{batch_id}/sources/reorder",
            json={"ordering": ["a"]},  # missing b
        )
        assert r.status_code == 400

    @pytest.mark.integration
    def test_create_validation_empty_title(self, client: TestClient) -> None:
        """Empty title → 422 (Pydantic min_length=1)."""
        r = client.post("/api/assistant/batches", json={"title": ""})
        assert r.status_code == 422
