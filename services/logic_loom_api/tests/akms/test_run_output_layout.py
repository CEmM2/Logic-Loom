"""Per-run output layout + metadata snapshot.

Covers: a run produces all four files at the expected paths:
        metadata.json, logs.txt, prompt_snapshot/<slot>.<ext>, and
        prompt_snapshot/effective_prompts.json.

Tier: unit.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from logic_loom_api.prompts.models import EffectivePromptSet
from logic_loom_api.runs.output_store import (
    EFFECTIVE_PROMPTS_FILE,
    LOG_FILENAME,
    METADATA_FILENAME,
    SNAPSHOT_DIRNAME,
    OutputStore,
    is_valid_run_id,
    make_run_id,
)


class TestRunOutputLayout:
    """Run directory naming, metadata shape and prompt snapshot."""

    @pytest.mark.unit
    def test_create_run_dir_naming(self, tmp_path):
        """run_id format: ``run_<YYYY-MM-DD_HHMM>_<slug-of-label>``.

        The created run directory exists at the canonical
        ``<workspace>/outputs/<batch_id>/<workflow_id>/<run_id>/`` path.
        """
        ws = tmp_path / "akms-workspace"
        ws.mkdir()
        # Pin ``now`` so the run_id is deterministic.
        fixed = datetime(2026, 5, 13, 10, 15, tzinfo=timezone.utc)
        run_id, run_dir = OutputStore.create_run_dir(
            ws,
            batch_id="fft-green-operators",
            workflow_id="akms.node_extraction",
            run_label="strict equations test 01",
            now=fixed,
        )
        # Format assertion.
        assert run_id == "run_2026-05-13_1015_strict-equations-test-01"
        assert is_valid_run_id(run_id)
        # Path layout: outputs/<batch>/<workflow>/<run_id>.
        expected = (
            ws / "outputs" / "fft-green-operators" / "akms.node_extraction" / run_id
        )
        assert run_dir == expected
        assert run_dir.exists()
        assert (run_dir / SNAPSHOT_DIRNAME).is_dir()
        assert (run_dir / LOG_FILENAME).exists()
        # An explicit run_id is honoured.
        explicit_id = "run_2026-05-13_1015_other-label"
        _, run_dir2 = OutputStore.create_run_dir(
            ws,
            batch_id="b",
            workflow_id="w",
            run_id=explicit_id,
        )
        assert run_dir2.name == explicit_id
        # Empty label falls back to ``run``.
        rid_empty = make_run_id(None, now=fixed)
        assert rid_empty.endswith("_run")

    @pytest.mark.unit
    def test_write_metadata_shape(self, tmp_path):
        """metadata.json has the documented shape.

        Atomic-rename ensures readers never see a partial file.
        """
        ws = tmp_path / "akms-workspace"
        ws.mkdir()
        _, run_dir = OutputStore.create_run_dir(
            ws,
            batch_id="batch-1",
            workflow_id="akms.node_extraction",
            run_label="t1",
            now=datetime(2026, 5, 13, 10, 15, tzinfo=timezone.utc),
        )
        payload = {
            "schema_version": 1,
            "run_id": run_dir.name,
            "batch_id": "batch-1",
            "workflow_id": "akms.node_extraction",
            "run_label": "t1",
            "status": "pending",
            "created_at": "2026-05-13T10:15:00+00:00",
            "executor": "claude",
            "notebook_id": None,
            "prompt_profile_id": None,
            "prompt_overrides": {},
            "effective_prompts": {
                "schema_version": 1,
                "slot_to_variant": {},
                "contents": {},
            },
            "output_dir": str(run_dir),
        }
        target = OutputStore.write_metadata(run_dir, payload)
        assert target == run_dir / METADATA_FILENAME
        assert target.exists()
        # No leftover tempfile.
        assert not target.with_suffix(".json.tmp").exists()
        # Round-trip.
        on_disk = json.loads(target.read_text(encoding="utf-8"))
        assert on_disk["run_id"] == run_dir.name
        assert on_disk["batch_id"] == "batch-1"
        assert on_disk["workflow_id"] == "akms.node_extraction"
        assert on_disk["schema_version"] == 1
        assert on_disk["effective_prompts"] == payload["effective_prompts"]
        # OutputStore.read_metadata is the symmetric reader.
        assert OutputStore.read_metadata(run_dir) == on_disk

    @pytest.mark.unit
    def test_write_prompt_snapshot_per_slot(self, tmp_path):
        """Each slot is written to its own file plus a combined effective_prompts.json.

        ``akms.*`` slots use ``.md``; ``zsum.*`` slots use ``.txt``.
        """
        ws = tmp_path / "akms-workspace"
        ws.mkdir()
        _, run_dir = OutputStore.create_run_dir(
            ws,
            batch_id="b",
            workflow_id="akms.node_extraction",
            run_label="snap",
            now=datetime(2026, 5, 13, 10, 15, tzinfo=timezone.utc),
        )
        eps = EffectivePromptSet(
            slot_to_variant={
                "akms.node_gen_skill": "user:akms.node_gen_skill.strict-equations",
                "zsum.collection_digest": "bundled:zsum.collection_digest.default",
            },
            contents={
                "akms.node_gen_skill": "# Strict equations\n\nbody",
                "zsum.collection_digest": "plain text",
            },
        )
        snap_dir = OutputStore.write_prompt_snapshot(run_dir, eps)
        assert snap_dir == run_dir / SNAPSHOT_DIRNAME

        # Per-slot files at the expected extensions.
        akms_file = snap_dir / "akms.node_gen_skill.md"
        zsum_file = snap_dir / "zsum.collection_digest.txt"
        assert akms_file.exists()
        assert zsum_file.exists()
        assert akms_file.read_text(encoding="utf-8") == "# Strict equations\n\nbody"
        assert zsum_file.read_text(encoding="utf-8") == "plain text"

        # Combined manifest.
        manifest_path = snap_dir / EFFECTIVE_PROMPTS_FILE
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["slot_to_variant"] == eps.slot_to_variant
        assert manifest["contents"] == eps.contents
        # No leftover tempfile.
        assert not manifest_path.with_suffix(".json.tmp").exists()
        # Symmetric reader.
        round_trip = OutputStore.read_effective_prompts(run_dir)
        assert round_trip == manifest
