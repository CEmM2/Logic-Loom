"""Run history endpoints + rerun.

Covers:
  Rerun creates a fresh run directory and writes a new snapshot.
  The source run is unchanged after rerun.

Tier: integration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.batches import BatchStore, EditableBatch


@pytest.fixture()
def runs_client(tmp_path, monkeypatch):
    """TestClient with an isolated akms_workspace + seeded batch."""
    ws = tmp_path / "akms-workspace"
    ws.mkdir()
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(ws))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))

    from logic_loom_api.main import api
    from logic_loom_api.runs.registry import RunRegistry

    store = BatchStore()
    batch = EditableBatch(id="batch-1", title="Test Batch")
    store.add(batch)
    store.save(ws)

    # Node-extraction now attaches a live executor; stub so history/rerun
    # tests do not spawn claude / require nlm in CI.
    monkeypatch.setattr(
        RunRegistry, "attach_workflow_subprocess", lambda self, *a, **k: None
    )

    with TestClient(api) as client:
        yield client, ws


def _start_one_run(client) -> dict:
    """Create one non-dry run via POST /api/runs/workflow."""
    body = {
        "batch_id": "batch-1",
        "workflow_id": "akms.node_extraction",
        "run_label": "alpha",
        "dry_run": False,
        # Avoid auto notebook create (needs `nlm` on PATH).
        "notebook_id": "nb-test",
    }
    resp = client.post("/api/runs/workflow", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestRunHistoryRerun:
    """Run history listing, rerun and effective-prompts snapshot."""

    @pytest.mark.integration
    def test_list_runs(self, runs_client):
        """GET /api/runs lists registered workflow runs.

        The static /workflow route must still resolve (no parametric capture).
        """
        client, _ws = runs_client

        # Static route still works (sanity — guards the route-order trap).
        # Dry-run so no executor is needed; this is just to
        # confirm /workflow isn't shadowed by /{run_id}.
        body = {
            "batch_id": "batch-1",
            "workflow_id": "akms.node_extraction",
            "dry_run": True,
        }
        sanity = client.post("/api/runs/workflow", json=body)
        assert sanity.status_code == 200, sanity.text

        # Start a real (non-dry) run so the registry records it.
        first = _start_one_run(client)
        assert first["status"] == "enqueued"
        run_id_a = first["run_id"]

        # List — both with and without kind filter.
        resp_all = client.get("/api/runs")
        assert resp_all.status_code == 200, resp_all.text
        all_runs = resp_all.json()["runs"]
        ids = [r["id"] for r in all_runs]
        assert run_id_a in ids

        resp_wf = client.get("/api/runs?kind=workflow")
        assert resp_wf.status_code == 200, resp_wf.text
        wf_runs = resp_wf.json()["runs"]
        assert all(r["kind"] == "workflow" for r in wf_runs)
        assert run_id_a in [r["id"] for r in wf_runs]

        # GET /api/runs/{id} returns the record.
        resp_one = client.get(f"/api/runs/{run_id_a}")
        assert resp_one.status_code == 200, resp_one.text
        assert resp_one.json()["id"] == run_id_a

        # 404 for unknown id.
        assert client.get("/api/runs/does-not-exist").status_code == 404

    @pytest.mark.integration
    def test_rerun_creates_fresh_dir_without_mutating_source(self, runs_client):
        """POST /api/runs/{id}/rerun creates a fresh run dir + snapshot,
        leaves the source run's directory and metadata untouched."""
        client, _ws = runs_client

        # Source run.
        src = _start_one_run(client)
        src_run_id = src["run_id"]
        src_dir = Path(src["out_dir"])
        src_meta_before = json.loads((src_dir / "metadata.json").read_text(encoding="utf-8"))
        src_mtime_before = (src_dir / "metadata.json").stat().st_mtime_ns

        # Rerun with a new label + an additional prompt override.
        resp = client.post(
            f"/api/runs/{src_run_id}/rerun",
            json={
                "run_label": "beta",
                "prompt_overrides": {
                    "akms.node_gen_skill": "user:akms.node_gen_skill.custom"
                },
            },
        )
        assert resp.status_code == 200, resp.text
        new = resp.json()

        # Fresh run dir.
        assert new["run_id"] != src_run_id
        new_dir = Path(new["out_dir"])
        assert new_dir.exists()
        assert new_dir != src_dir
        # New metadata + snapshot exist.
        new_meta = json.loads((new_dir / "metadata.json").read_text(encoding="utf-8"))
        assert new_meta["run_id"] == new["run_id"]
        assert new_meta["run_label"] == "beta"
        # Merged override survives.
        assert new_meta["prompt_overrides"][
            "akms.node_gen_skill"
        ] == "user:akms.node_gen_skill.custom"
        # Snapshot manifest written.
        assert (new_dir / "prompt_snapshot" / "effective_prompts.json").exists()

        # Source untouched.
        src_meta_after = json.loads((src_dir / "metadata.json").read_text(encoding="utf-8"))
        assert src_meta_after == src_meta_before
        # mtime unchanged (the rerun never opened the source metadata for write).
        assert (src_dir / "metadata.json").stat().st_mtime_ns == src_mtime_before

    @pytest.mark.integration
    def test_effective_prompts_returns_snapshot(self, runs_client):
        """GET /api/runs/{id}/effective-prompts reads from disk snapshot."""
        client, _ws = runs_client
        rec = _start_one_run(client)
        run_id = rec["run_id"]

        resp = client.get(f"/api/runs/{run_id}/effective-prompts")
        assert resp.status_code == 200, resp.text
        manifest = resp.json()
        assert "slot_to_variant" in manifest
        assert "contents" in manifest
        # The endpoint reads from disk — confirm by mutating the on-disk file
        # and observing the change reflected in a subsequent request.
        run_dir = Path(rec["out_dir"])
        snap = run_dir / "prompt_snapshot" / "effective_prompts.json"
        data = json.loads(snap.read_text(encoding="utf-8"))
        data["slot_to_variant"]["__sentinel__"] = "value"
        snap.write_text(json.dumps(data), encoding="utf-8")

        resp2 = client.get(f"/api/runs/{run_id}/effective-prompts")
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["slot_to_variant"].get("__sentinel__") == "value"
