"""Tests for the starter-batch seed location & schema.

Covers:
    1 (placeholder seed loads via EditableBatch.model_validate and survives a
       model_dump round-trip),
    2 (seed directory present in a built wheel — force-include),
    3 (EditableBatch.origin defaults so existing user batches round-trip unchanged).

Related existing coverage: tests/akms/test_batch_store.py (base EditableBatch
round-trip, pre-origin field).
"""

from __future__ import annotations

import json
from importlib import resources

import pytest

from logic_loom_api.batches.models import BatchOrigin, EditableBatch

# Package path for the bundled starter-batch defaults — mirrors the
# BUNDLED_RESOURCE_PACKAGE convention from prompts/store.py and
# workflows/builtin.py.
_SEED_PACKAGE = "logic_loom_api.resources.akms_batches.defaults"
_PLACEHOLDER_FILENAME = "starter-placeholder.json"


def _load_placeholder() -> dict:
    """Load the placeholder fixture from the bundled resources."""
    seed_root = resources.files(_SEED_PACKAGE)
    fixture_file = seed_root.joinpath(_PLACEHOLDER_FILENAME)
    return json.loads(fixture_file.read_text(encoding="utf-8"))


class TestStarterBatchSeedSchema:
    """Unit tests for the starter-batch seed schema, origin field, packaging."""

    @pytest.mark.unit
    def test_placeholder_seed_round_trips(self):
        """Verifies: the shipped placeholder seed loads and round-trips.
        EditableBatch.model_validate(load(fixture)) then model_dump()
        equals the loaded dict. Passes when the round-trip is lossless."""
        raw = _load_placeholder()
        batch = EditableBatch.model_validate(raw)
        dumped = batch.model_dump()
        assert dumped == raw, (
            "model_dump() output differed from the on-disk fixture — "
            "the fixture is not a canonical model_dump() serialization"
        )

    @pytest.mark.unit
    def test_origin_default_backward_compatible(self):
        """Verifies: the new origin field has a deterministic default and a
        batch dict with no origin key still validates.
        Existing user batches round-trip unchanged. Passes when a
        legacy batch (no origin) validates and dumps stably."""
        # A legacy on-disk batch has no 'origin' key at all.
        legacy = {"id": "old-batch", "title": "Old Batch"}
        batch = EditableBatch.model_validate(legacy)
        # Default must be 'user' so existing user batches are unaffected.
        assert batch.origin == "user", (
            f"EditableBatch.origin default should be 'user', got {batch.origin!r}"
        )
        # Round-trip is stable: dumping and re-validating gives identity.
        dumped = batch.model_dump()
        assert EditableBatch.model_validate(dumped) == batch

    @pytest.mark.unit
    def test_placeholder_seed_marked_starter(self):
        """Verifies: the placeholder starter seed carries origin == 'starter'.
        The origin marker distinguishes shipped seeds from user
        batches. Passes when the loaded seed's origin is 'starter'."""
        raw = _load_placeholder()
        batch = EditableBatch.model_validate(raw)
        assert batch.origin == "starter", (
            f"Placeholder seed should carry origin='starter', got {batch.origin!r}"
        )

    @pytest.mark.unit
    def test_seed_directory_is_packaged(self):
        """Verifies: resources/akms_batches/defaults/ is discoverable via
        importlib.resources (proxy for wheel force-include coverage).
        The seed directory ships. Passes when the traversable resolves
        and lists the placeholder fixture."""
        seed_root = resources.files(_SEED_PACKAGE)
        assert seed_root.is_dir(), (
            f"Bundled seed directory not found at package {_SEED_PACKAGE!r}; "
            "wheel force-include may be missing"
        )
        names = {child.name for child in seed_root.iterdir()}
        assert _PLACEHOLDER_FILENAME in names, (
            f"Expected {_PLACEHOLDER_FILENAME!r} in seed directory, found: {names!r}"
        )
