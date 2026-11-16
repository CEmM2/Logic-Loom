"""Workflow Library API.

Covers: built-in workflows are listed even with an empty user-workflows folder.
Plus integration coverage for duplicate semantics (a duplicate of a built-in
must materialise a new user workflow on disk without mutating the built-in).

Tier: integration.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def workspace_client(tmp_path, monkeypatch):
    """TestClient with an isolated akms_workspace.

    Resets ``Settings`` via env vars so the routes pick up tmp_path on each
    call (the routes call ``Settings.load()`` per request).
    """
    ws = tmp_path / "akms-workspace"
    ws.mkdir()
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(ws))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))
    from logic_loom_api.main import api

    with TestClient(api) as client:
        yield client, ws


class TestWorkflowLibraryApi:
    """Workflow Library API: list, duplicate, create, update, archive."""

    @pytest.mark.integration
    def test_list_returns_four_builtins(self, workspace_client):
        """List returns the four built-ins on an empty workspace."""
        client, ws = workspace_client
        user_dir = ws / "workflow-library" / "user-workflows"
        if user_dir.exists():
            assert not any(user_dir.iterdir())

        resp = client.get("/api/workflows")
        assert resp.status_code == 200, resp.text
        items = resp.json()["workflows"]
        ids = {w["id"] for w in items}
        expected = {
            "akms.node_extraction",
            "research.literature_survey",
            "writing.paper_introduction",
            "research.proposal_ideas",
        }
        assert expected <= ids, f"missing built-ins; got {ids}"
        bundled = [w for w in items if w["id"] in expected]
        assert all(w["source"] == "bundled" for w in bundled)

    @pytest.mark.integration
    def test_duplicate_creates_user_workflow_on_disk(self, workspace_client):
        """Duplicate of a built-in materialises a user workflow on disk and
        does not mutate the built-in."""
        client, ws = workspace_client
        resp = client.post("/api/workflows/akms.node_extraction/duplicate")
        assert resp.status_code == 200, resp.text
        dup = resp.json()
        assert dup["id"].startswith("akms.node_extraction")
        assert dup["id"] != "akms.node_extraction"
        assert dup["source"] == "user"
        assert dup["status"] == "active"

        user_dir = ws / "workflow-library" / "user-workflows"
        path = user_dir / f"{dup['id']}.json"
        assert path.exists(), f"expected duplicate at {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["id"] == dup["id"]
        assert "claude" in data["required_external_tools"]
        assert "nlm" in data["required_external_tools"]

        orig = client.get("/api/workflows/akms.node_extraction").json()
        assert orig["source"] == "bundled"
        assert orig["id"] == "akms.node_extraction"

    @pytest.mark.integration
    def test_archive_builtin_rejected(self, workspace_client):
        """Built-in workflows cannot be archived directly."""
        client, _ = workspace_client
        resp = client.post("/api/workflows/akms.node_extraction/archive")
        assert resp.status_code == 409, resp.text

    @pytest.mark.integration
    def test_create_then_archive_user_workflow(self, workspace_client):
        """Round-trip: create -> archive -> filtered from default list."""
        client, _ = workspace_client
        body = {
            "name": "Custom Survey",
            "executor": "claude",
            "source_mode": "none",
            "prompt_slots": {"main": "workflow.custom.main"},
        }
        resp = client.post("/api/workflows", json=body)
        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]
        assert new_id

        resp = client.post(f"/api/workflows/{new_id}/archive")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "archived"

        listing = client.get("/api/workflows").json()["workflows"]
        assert all(w["id"] != new_id for w in listing)

        listing_arch = client.get(
            "/api/workflows", params={"include_archived": "true"}
        ).json()["workflows"]
        assert any(w["id"] == new_id for w in listing_arch)

    @pytest.mark.integration
    def test_create_rejects_duplicate_id(self, workspace_client):
        """Creating a workflow whose id matches a built-in is a 409."""
        client, _ = workspace_client
        body = {
            "id": "akms.node_extraction",
            "name": "Shadow",
            "executor": "claude",
        }
        resp = client.post("/api/workflows", json=body)
        assert resp.status_code == 409, resp.text

    @pytest.mark.integration
    def test_update_builtin_rejected(self, workspace_client):
        """PUT on a built-in is a 409 (must duplicate first)."""
        client, _ = workspace_client
        body = {"name": "Modified", "executor": "claude"}
        resp = client.put("/api/workflows/akms.node_extraction", json=body)
        assert resp.status_code == 409, resp.text
