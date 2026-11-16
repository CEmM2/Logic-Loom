"""Batch CRUD API.

Covers:
  Each endpoint responds with the documented shape.
  Duplicate produces a new id while preserving content.

Tier: integration. Uses FastAPI TestClient against the mounted
``/api/batches`` router. Workspace isolation via
``LOGIC_LOOM_AKMS_WORKSPACE`` env override into ``tmp_path``.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """A TestClient with an isolated AKMS workspace under tmp_path."""
    workspace = tmp_path / "akms-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "prompt-library").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(workspace))
    # Reload main so the mount picks up the env var if it was set at import
    # time elsewhere. (Same pattern as test_picker_mount.py.)
    import logic_loom_api.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.api)


def _create(client: TestClient, **body):
    body.setdefault("title", "FFT Green Operators")
    r = client.post("/api/batches", json=body)
    assert r.status_code == 201, r.text
    return r.json()


class TestFullCRUDRoundTrip:
    """Endpoint shapes + persistence."""

    def test_create_get_list(self, client: TestClient):
        created = _create(client, title="FFT Green Operators")
        assert created["id"] == "fft-green-operators"
        assert created["status"] == "active"
        assert created["source"] == "user"
        assert created["generation"]["nlm_timeout"] == 540

        r = client.get(f"/api/batches/{created['id']}")
        assert r.status_code == 200
        assert r.json() == created

        r = client.get("/api/batches")
        assert r.status_code == 200
        assert [b["id"] for b in r.json()["batches"]] == [created["id"]]

    def test_create_blank_title_400(self, client: TestClient):
        r = client.post("/api/batches", json={"title": "   "})
        # Pydantic min_length validator catches the empty title.
        assert r.status_code in (400, 422)

    def test_patch_updates_only_supplied_fields(self, client: TestClient):
        created = _create(client, title="A batch", description="orig")
        r = client.patch(
            f"/api/batches/{created['id']}",
            json={"description": "new", "round": 4},
        )
        assert r.status_code == 200
        got = r.json()
        assert got["description"] == "new"
        assert got["round"] == 4
        assert got["title"] == "A batch"
        assert got["updated_at"] >= created["updated_at"]

    def test_archive_sets_status(self, client: TestClient):
        created = _create(client, title="Archivable")
        r = client.post(f"/api/batches/{created['id']}/archive")
        assert r.status_code == 200
        assert r.json()["status"] == "archived"

    def test_delete_removes_record(self, client: TestClient):
        created = _create(client, title="Doomed")
        r = client.delete(f"/api/batches/{created['id']}")
        assert r.status_code == 204
        r = client.get(f"/api/batches/{created['id']}")
        assert r.status_code == 404

    def test_get_missing_404(self, client: TestClient):
        r = client.get("/api/batches/no-such")
        assert r.status_code == 404

    def test_papers_add_remove_move(self, client: TestClient):
        created = _create(client, title="Papers", papers=["a", "b", "c"])
        r = client.post(
            f"/api/batches/{created['id']}/papers/add", json={"papers": ["d"]}
        )
        assert r.json()["papers"] == ["a", "b", "c", "d"]

        r = client.post(
            f"/api/batches/{created['id']}/papers/remove", json={"papers": ["b"]}
        )
        assert r.json()["papers"] == ["a", "c", "d"]

        r = client.post(
            f"/api/batches/{created['id']}/papers/move",
            json={"paper": "d", "to_index": 0},
        )
        assert r.json()["papers"] == ["d", "a", "c"]

    def test_papers_move_unknown_paper_400(self, client: TestClient):
        created = _create(client, title="Movable", papers=["a"])
        r = client.post(
            f"/api/batches/{created['id']}/papers/move",
            json={"paper": "ghost", "to_index": 0},
        )
        assert r.status_code == 400

    def test_bulk_add_appends_both(self, client: TestClient):
        created = _create(client, title="BulkA")
        r = client.post(
            f"/api/batches/{created['id']}/bulk-add",
            json={
                "papers": ["p1"],
                "nodes": [{"node_id": "n1", "title": "Node 1"}],
            },
        )
        assert r.status_code == 200
        got = r.json()
        assert got["papers"] == ["p1"]
        assert [n["node_id"] for n in got["nodes"]] == ["n1"]

    def test_bulk_add_empty_400(self, client: TestClient):
        created = _create(client, title="BulkB")
        r = client.post(
            f"/api/batches/{created['id']}/bulk-add",
            json={"papers": [], "nodes": []},
        )
        assert r.status_code == 400

    def test_node_crud(self, client: TestClient):
        created = _create(client, title="Nodey")
        bid = created["id"]
        r = client.post(
            f"/api/batches/{bid}/nodes",
            json={"node_id": "n1", "title": "Node 1"},
        )
        assert r.status_code == 201
        assert [n["node_id"] for n in r.json()["nodes"]] == ["n1"]

        # Duplicate node id => 409.
        r = client.post(
            f"/api/batches/{bid}/nodes",
            json={"node_id": "n1", "title": "Dup"},
        )
        assert r.status_code == 409

        r = client.patch(
            f"/api/batches/{bid}/nodes/n1",
            json={"description": "now described", "priority": 5},
        )
        assert r.status_code == 200
        n = next(n for n in r.json()["nodes"] if n["node_id"] == "n1")
        assert n["description"] == "now described"
        assert n["priority"] == 5

        r = client.delete(f"/api/batches/{bid}/nodes/n1")
        assert r.status_code == 204
        r = client.get(f"/api/batches/{bid}")
        assert r.json()["nodes"] == []

    def test_prompts_get_put_reset(self, client: TestClient):
        created = _create(client, title="Promptly")
        bid = created["id"]
        # Initial GET — both empty.
        r = client.get(f"/api/batches/{bid}/prompts")
        assert r.status_code == 200
        assert r.json() == {"prompt_profile_id": None, "prompt_overrides": {}}

        # PUT — set both.
        r = client.put(
            f"/api/batches/{bid}/prompts",
            json={
                "prompt_profile_id": "user:fft",
                "prompt_overrides": {
                    "akms.node_gen_skill": "user:akms.node_gen_skill.strict"
                },
            },
        )
        assert r.status_code == 200
        assert r.json()["prompt_profile_id"] == "user:fft"

        # Confirm persisted via fresh GET.
        r = client.get(f"/api/batches/{bid}/prompts")
        assert r.json()["prompt_overrides"] == {
            "akms.node_gen_skill": "user:akms.node_gen_skill.strict"
        }

        # Reset — both back to defaults.
        r = client.post(f"/api/batches/{bid}/prompts/reset")
        assert r.status_code == 200
        assert r.json() == {"prompt_profile_id": None, "prompt_overrides": {}}

    def test_persistence_round_trips_disk(self, client: TestClient, tmp_path: Path):
        created = _create(client, title="Persist")
        # Reach into the workspace and verify batches.json.
        workspace = tmp_path / "akms-workspace"
        on_disk = json.loads((workspace / "batches.json").read_text())
        assert on_disk["schema_version"] == 1
        assert on_disk["batches"][0]["id"] == created["id"]


class TestDuplicateProducesNewId:
    """Duplicate mints a new id while preserving content."""

    def test_duplicate_yields_distinct_id(self, client: TestClient):
        src = _create(
            client,
            title="Dupable",
            papers=["a", "b"],
            nodes=[{"node_id": "n1", "title": "Node 1"}],
            prompt_profile_id="user:fft",
            prompt_overrides={"akms.node_gen_skill": "user:x.y.z"},
        )
        r = client.post(f"/api/batches/{src['id']}/duplicate")
        assert r.status_code == 201
        dup = r.json()
        assert dup["id"] != src["id"]
        assert dup["id"].startswith(src["id"]) or dup["id"] == "dupable-2"
        assert dup["source"] == "duplicated"
        # Content fields preserved.
        assert dup["title"] == src["title"]
        assert dup["papers"] == src["papers"]
        assert dup["nodes"] == src["nodes"]
        assert dup["prompt_profile_id"] == src["prompt_profile_id"]
        assert dup["prompt_overrides"] == src["prompt_overrides"]

    def test_duplicate_is_independent(self, client: TestClient):
        """Mutating the duplicate must not affect the source (deep copy)."""
        src = _create(client, title="Indep", papers=["a"])
        r = client.post(f"/api/batches/{src['id']}/duplicate")
        dup_id = r.json()["id"]
        client.post(
            f"/api/batches/{dup_id}/papers/add", json={"papers": ["new-only"]}
        )
        r = client.get(f"/api/batches/{src['id']}")
        assert r.json()["papers"] == ["a"]


class TestEffectivePromptsCallsResolver:
    """/effective-prompts delegates to resolve_prompts_for_run."""

    def test_effective_prompts_returns_shape(self, client: TestClient):
        created = _create(client, title="Effective")
        r = client.get(f"/api/batches/{created['id']}/effective-prompts")
        assert r.status_code == 200
        body = r.json()
        # EffectivePromptSet shape (slot_to_variant + contents).
        assert "slot_to_variant" in body
        assert "contents" in body
        assert body["schema_version"] == 1

    def test_effective_prompts_respects_overrides(self, client: TestClient):
        created = _create(
            client,
            title="OverrideMe",
            prompt_overrides={
                "akms.node_gen_skill": "user:akms.node_gen_skill.custom"
            },
        )
        r = client.get(f"/api/batches/{created['id']}/effective-prompts")
        assert r.status_code == 200
        assert (
            r.json()["slot_to_variant"]["akms.node_gen_skill"]
            == "user:akms.node_gen_skill.custom"
        )
