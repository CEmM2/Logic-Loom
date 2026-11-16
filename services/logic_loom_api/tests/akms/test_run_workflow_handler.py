"""Run-workflow request handler.

Covers: ``dry_run=true`` returns a payload with the resolved variants and
        the would-be output_dir, without spawning a subprocess.
Plus integration coverage for the ``save_as_batch_default=true`` mutation
path (the only mechanism by which a run mutates a batch JSON).

Tier: integration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.batches import EditableBatch


@pytest.fixture()
def runs_client(tmp_path, monkeypatch):
    """TestClient with an isolated akms_workspace + seeded batch."""
    ws = tmp_path / "akms-workspace"
    ws.mkdir()
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(ws))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))

    from logic_loom_api.batches import BatchStore
    from logic_loom_api.main import api
    from logic_loom_api.runs.registry import RunRegistry

    store = BatchStore()
    batch = EditableBatch(id="batch-1", title="Test Batch")
    store.add(batch)
    store.save(ws)

    # Non-dry node_extraction now attaches an executor; stub so this suite
    # does not require claude/nlm binaries (CI).
    monkeypatch.setattr(
        RunRegistry, "attach_workflow_subprocess", lambda self, *a, **k: None
    )

    with TestClient(api) as client:
        yield client, ws


class TestRunWorkflowHandler:
    """Run-workflow handler: dry run, save-as-default, errors, run dir."""

    @pytest.mark.integration
    def test_dry_run_emits_effective_prompts(self, runs_client):
        """dry_run=true returns resolved variants + would-be out_dir."""
        client, _ = runs_client
        body = {
            "batch_id": "batch-1",
            "workflow_id": "akms.node_extraction",
            "dry_run": True,
        }
        resp = client.post("/api/runs/workflow", json=body)
        assert resp.status_code == 200, resp.text
        payload = resp.json()

        assert payload["status"] == "preview"
        assert payload["dry_run"] is True
        assert payload["batch_id"] == "batch-1"
        assert payload["workflow_id"] == "akms.node_extraction"
        assert payload["executor"] == "claude"

        eps = payload["effective_prompts"]
        assert "slot_to_variant" in eps
        assert "contents" in eps

        assert payload["out_dir"]
        out_dir = Path(payload["out_dir"])
        assert not out_dir.exists(), "dry_run must NOT create the run dir"

    @pytest.mark.integration
    def test_save_as_default_mutates_batch(self, runs_client):
        """``save_as_batch_default=true`` writes overrides into batches.json."""
        client, ws = runs_client
        body = {
            "batch_id": "batch-1",
            "workflow_id": "akms.node_extraction",
            "save_as_batch_default": True,
            "prompt_overrides": {
                "akms.node_gen_skill": "user:akms.node_gen_skill.custom"
            },
            "dry_run": True,
        }
        resp = client.post("/api/runs/workflow", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["saved_as_default"] is True

        bj = json.loads((ws / "batches.json").read_text(encoding="utf-8"))
        batch = next(b for b in bj["batches"] if b["id"] == "batch-1")
        assert batch["prompt_overrides"][
            "akms.node_gen_skill"
        ] == "user:akms.node_gen_skill.custom"

    @pytest.mark.integration
    def test_unknown_batch_returns_404(self, runs_client):
        """Missing batch -> 404."""
        client, _ = runs_client
        body = {"batch_id": "nope", "workflow_id": "akms.node_extraction"}
        resp = client.post("/api/runs/workflow", json=body)
        assert resp.status_code == 404, resp.text

    @pytest.mark.integration
    def test_unknown_workflow_returns_404(self, runs_client):
        """Missing workflow -> 404."""
        client, _ = runs_client
        body = {"batch_id": "batch-1", "workflow_id": "nope.no_such"}
        resp = client.post("/api/runs/workflow", json=body)
        assert resp.status_code == 404, resp.text

    @pytest.mark.integration
    def test_non_dry_run_creates_run_dir(self, runs_client):
        """Non-dry runs materialise the run directory (the run output layout fills it)."""
        client, _ = runs_client
        body = {
            "batch_id": "batch-1",
            "workflow_id": "akms.node_extraction",
            "dry_run": False,
            # Provide a notebook id so the launch path does not require
            # `nlm notebook create` (unavailable in CI).
            "notebook_id": "nb-test",
        }
        resp = client.post("/api/runs/workflow", json=body)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["status"] == "enqueued"
        out_dir = Path(payload["out_dir"])
        assert out_dir.exists(), "non-dry run must create the run dir"
