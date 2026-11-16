"""Tests for first-run starter-batch seeding into the AKMS workspace.

Covers:
    1 (empty workspace -> starter batches present after bootstrap),
    2 (non-empty workspace -> untouched / idempotent re-seed),
    3 (seed failure -> sidecar still comes up),
    4 (seed_starter_batches=False -> no seeding).

Invariant under test: idempotent, id-skip, degrade-gracefully (try/except
guard mirrors bootstrap_akms_workspace / the lifespan wrappers).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from logic_loom_api.akms_workspace import (
    _SEED_RESOURCE_PACKAGE,
    _seed_starter_batches,
    bootstrap_akms_workspace,
)
from logic_loom_api.batches.models import EditableBatch
from logic_loom_api.batches.store import BatchStore
from logic_loom_api.config import Settings


def _settings(tmp_path: Path, **overrides) -> Settings:
    """Build a minimal Settings pointing at tmp_path for the workspace."""
    return Settings.load(
        config_path=tmp_path / "missing.toml",
        akms_workspace=tmp_path / "akms-workspace",
        cache_path=tmp_path / "cache",
        **overrides,
    )


def _starter_ids() -> set[str]:
    """Return the set of ids shipped in the seed pack.

    Reads the actual bundled resource so the test stays in sync with the
    resource without hard-coding the list.
    """
    from importlib import resources

    from pydantic import ValidationError

    try:
        pkg_root = resources.files(_SEED_RESOURCE_PACKAGE)
    except (ModuleNotFoundError, FileNotFoundError):
        return set()
    ids: set[str] = set()
    for child in pkg_root.iterdir():
        if not child.name.endswith(".json"):
            continue
        try:
            batch = EditableBatch.model_validate_json(child.read_text(encoding="utf-8"))
            ids.add(batch.id)
        except (OSError, ValidationError, ValueError):
            pass
    return ids


class TestStarterBatchSeeding:
    """Integration tests for first-run seeding on empty workspace."""

    @pytest.mark.integration
    def test_empty_workspace_gets_seeded(self, tmp_path: Path):
        """Verifies: bootstrapping an empty tmp akms_workspace seeds the
        starter batches. Passes when batches.json holds the shipped
        starter ids after bootstrap."""
        settings = _settings(tmp_path)
        ws = settings.akms_workspace.expanduser()

        bootstrap_akms_workspace(settings)

        store = BatchStore.load(ws)
        expected_ids = _starter_ids()
        # At least one seed shipped; confirm all present.
        assert expected_ids, "No seed ids found — check the defaults package"
        actual_ids = set(store.ids())
        assert expected_ids <= actual_ids, (
            f"Missing seed ids after bootstrap: {expected_ids - actual_ids}"
        )
        # All seeded batches carry origin="starter"
        for batch in store.batches:
            if batch.id in expected_ids:
                assert batch.origin == "starter", (
                    f"Batch {batch.id!r} has origin={batch.origin!r}, expected 'starter'"
                )

    @pytest.mark.integration
    def test_reseed_is_idempotent(self, tmp_path: Path):
        """Verifies: a second bootstrap of the same workspace adds no
        duplicate ids (idempotency). Passes when the batch count is
        unchanged after re-bootstrap."""
        settings = _settings(tmp_path)
        ws = settings.akms_workspace.expanduser()

        bootstrap_akms_workspace(settings)
        count_after_first = len(BatchStore.load(ws).ids())

        # Second bootstrap — must be a no-op.
        bootstrap_akms_workspace(settings)
        count_after_second = len(BatchStore.load(ws).ids())

        assert count_after_second == count_after_first, (
            f"Re-seed added batches: {count_after_first} -> {count_after_second}"
        )

    @pytest.mark.integration
    def test_colliding_user_batch_survives(self, tmp_path: Path):
        """Verifies: a pre-existing batch whose id collides with a starter
        id is NOT overwritten (never clobber). Passes when the user's
        batch content is byte-for-byte unchanged after bootstrap."""
        settings = _settings(tmp_path)
        ws = settings.akms_workspace.expanduser()
        ws.mkdir(parents=True, exist_ok=True)

        # Pick a seed id we know will be shipped and pre-populate the store
        # with a user-authored batch that uses that id.
        seed_ids = _starter_ids()
        assert seed_ids, "No seed ids in the defaults package"
        colliding_id = next(iter(sorted(seed_ids)))

        user_batch = EditableBatch(
            id=colliding_id,
            title="My Custom Batch (user-owned)",
            description="This must survive the bootstrap untouched.",
            origin="user",
        )
        initial_store = BatchStore(batches=[user_batch])
        initial_store.save(ws)
        original_json = BatchStore.load(ws).get(colliding_id)
        assert original_json is not None

        # Bootstrap — the store is non-empty, so NO seeding should occur at all.
        bootstrap_akms_workspace(settings)

        store_after = BatchStore.load(ws)
        surviving = store_after.get(colliding_id)
        assert surviving is not None, "Pre-existing batch was removed"
        assert surviving.title == user_batch.title, (
            f"User batch title was overwritten: {surviving.title!r}"
        )
        assert surviving.description == user_batch.description, (
            f"User batch description was overwritten: {surviving.description!r}"
        )
        assert surviving.origin == "user", (
            f"User batch origin was changed to {surviving.origin!r}"
        )

    @pytest.mark.integration
    def test_seed_failure_does_not_block_startup(self, tmp_path: Path):
        """Verifies: a seed-load error is swallowed and bootstrap completes.
        Degrade-gracefully: passes when bootstrap returns without
        raising despite a forced seed error."""
        settings = _settings(tmp_path)

        # Force _seed_starter_batches to raise an arbitrary exception.
        # bootstrap_akms_workspace must catch it and log at WARNING.
        with patch(
            "logic_loom_api.akms_workspace._seed_starter_batches",
            side_effect=RuntimeError("simulated seed failure"),
        ):
            # Must NOT raise — the sidecar degrade-gracefully contract.
            result = bootstrap_akms_workspace(settings)

        assert result is not None, "bootstrap_akms_workspace returned None"
        # Workspace root must still exist (bootstrap ran to completion).
        assert result.exists(), f"Workspace root not created: {result}"

    @pytest.mark.integration
    def test_opt_out_setting_disables_seeding(self, tmp_path: Path):
        """Verifies: seed_starter_batches=False leaves the store empty.
        Opt-out: passes when no starter batches are present after
        bootstrap with the setting off."""
        settings = _settings(tmp_path, seed_starter_batches=False)
        ws = settings.akms_workspace.expanduser()

        bootstrap_akms_workspace(settings)

        store = BatchStore.load(ws)
        assert store.batches == [], (
            f"Expected empty store with seed_starter_batches=False, "
            f"got {[b.id for b in store.batches]}"
        )
