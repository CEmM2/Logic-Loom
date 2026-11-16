"""Run registry extension (workflow runs).

Covers: existing zsum run history loads identically after the migration.
Plus integration coverage for the (batch_id, workflow_id, run_id) triplet
lookup that the AKMS node-generation routes and the Runs page depend on.

Tier: regression.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logic_loom_api.runs.models import RunRecord
from logic_loom_api.runs.registry import RunRegistry


class TestRunRegistryWorkflowRuns:
    """Run registry: legacy zsum records + workflow-run records."""

    @pytest.mark.regression
    def test_pre_migration_snapshot_round_trips(self, tmp_path):
        """Legacy zsum records (no ``kind`` field) load unchanged.

        Synthesises an old-style ``index.json`` with a single zsum record
        missing the ``kind`` / ``batch_id`` / ``workflow_id`` fields, then
        boots a fresh :class:`RunRegistry` against it. The loaded record
        must default ``kind="zsum"`` and preserve every other field.
        """
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        # Legacy shape (before workflow runs): no kind / batch_id / workflow_id keys.
        legacy = {
            "runs": [
                {
                    "id": "legacy-zsum-1",
                    "stage": "paper-card",
                    "status": "succeeded",
                    "started_at": "2026-04-01T00:00:00+00:00",
                    "ended_at": "2026-04-01T00:01:00+00:00",
                    "exit_code": 0,
                    "pid": 12345,
                    "log_path": "/tmp/legacy.log",
                    "args": ["uv", "run", "zsum", "paper-card"],
                    "cwd": "/tmp/zsum-repo",
                    "last_lines": ["[INFO] done"],
                }
            ]
        }
        (runs_dir / "index.json").write_text(
            json.dumps(legacy), encoding="utf-8"
        )

        reg = RunRegistry(tmp_path, zsum_repo=None)
        rec = reg.get("legacy-zsum-1")
        assert rec is not None
        # All legacy fields preserved.
        assert rec.id == "legacy-zsum-1"
        assert rec.stage == "paper-card"
        # NOTE: the registry marks "running"/"pending" stale runs as
        # "failed" on load; this record was already "succeeded" so it must
        # round-trip as "succeeded".
        assert rec.status == "succeeded"
        assert rec.exit_code == 0
        assert rec.pid == 12345
        assert rec.log_path == "/tmp/legacy.log"
        assert rec.args == ["uv", "run", "zsum", "paper-card"]
        assert rec.cwd == "/tmp/zsum-repo"
        assert rec.last_lines == ["[INFO] done"]
        # Workflow-run fields default for legacy records.
        assert rec.kind == "zsum"
        assert rec.batch_id is None
        assert rec.workflow_id is None

    @pytest.mark.regression
    def test_workflow_run_indexable_by_triplet(self, tmp_path):
        """Workflow runs are addressable by ``(batch_id, workflow_id, run_id)``."""
        reg = RunRegistry(tmp_path, zsum_repo=None)
        rec = reg.register_workflow_run(
            batch_id="batch-1",
            workflow_id="akms.node_extraction",
            run_id="r-abc123",
            log_path=str(tmp_path / "r-abc123.log"),
            args=["claude", "--workflow", "akms.node_extraction"],
            cwd=str(tmp_path),
        )
        assert rec.kind == "workflow"
        assert rec.batch_id == "batch-1"
        assert rec.workflow_id == "akms.node_extraction"

        # Triplet lookup matches.
        found = reg.get_by_triplet(
            "batch-1", "akms.node_extraction", "r-abc123"
        )
        assert found is not None
        assert found.id == "r-abc123"

        # Triplet lookup with wrong batch/workflow returns None.
        assert reg.get_by_triplet(
            "other-batch", "akms.node_extraction", "r-abc123"
        ) is None
        assert reg.get_by_triplet(
            "batch-1", "other.workflow", "r-abc123"
        ) is None

        # list_workflow_runs filters work.
        all_workflow = reg.list_workflow_runs()
        assert len(all_workflow) == 1
        by_batch = reg.list_workflow_runs(batch_id="batch-1")
        assert len(by_batch) == 1
        by_wf = reg.list_workflow_runs(workflow_id="akms.node_extraction")
        assert len(by_wf) == 1
        nothing = reg.list_workflow_runs(batch_id="nope")
        assert nothing == []

        # list(kind="workflow") filter.
        assert len(reg.list(kind="workflow")) == 1
        assert len(reg.list(kind="zsum")) == 0

    @pytest.mark.regression
    def test_mixed_kinds_persist_round_trip(self, tmp_path):
        """A registry with both zsum and workflow records reloads identically."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        # Seed legacy zsum + new workflow on disk.
        seed = {
            "runs": [
                {
                    "id": "z1",
                    "stage": "paper-card",
                    "status": "succeeded",
                    "started_at": "2026-04-01T00:00:00+00:00",
                    "log_path": "/tmp/z1.log",
                    # No kind / batch_id / workflow_id.
                },
                {
                    "id": "w1",
                    "stage": "akms.node_extraction",
                    "status": "succeeded",
                    "started_at": "2026-04-02T00:00:00+00:00",
                    "log_path": "/tmp/w1.log",
                    "kind": "workflow",
                    "batch_id": "batch-1",
                    "workflow_id": "akms.node_extraction",
                },
            ]
        }
        (runs_dir / "index.json").write_text(json.dumps(seed), encoding="utf-8")

        reg = RunRegistry(tmp_path, zsum_repo=None)
        z = reg.get("z1")
        w = reg.get("w1")
        assert z is not None and z.kind == "zsum"
        assert w is not None and w.kind == "workflow"
        assert w.batch_id == "batch-1"
        assert w.workflow_id == "akms.node_extraction"

    @pytest.mark.regression
    def test_register_workflow_run_persists_index(self, tmp_path):
        """``register_workflow_run`` writes the record to index.json immediately."""
        reg = RunRegistry(tmp_path, zsum_repo=None)
        reg.register_workflow_run(
            batch_id="b1",
            workflow_id="wf.test",
            run_id="rid",
            log_path=str(tmp_path / "rid.log"),
        )
        index = json.loads(
            (tmp_path / "runs" / "index.json").read_text(encoding="utf-8")
        )
        rec = next(r for r in index["runs"] if r["id"] == "rid")
        assert rec["kind"] == "workflow"
        assert rec["batch_id"] == "b1"
        assert rec["workflow_id"] == "wf.test"
