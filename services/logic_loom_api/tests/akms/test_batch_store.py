"""Batch schema + on-disk store.

Covers: a BatchStore round-trips through JSON without information loss;
slug collision behaviour is deterministic.

Tier: unit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logic_loom_api.batches import (
    BatchGenerationDefaults,
    BatchNode,
    BatchStore,
    EditableBatch,
    SCHEMA_VERSION,
    slugify_title,
)


def _fixture_batch(
    *,
    title: str = "FFT Green Operators",
    id_: str | None = None,
    **overrides,
) -> EditableBatch:
    """Build a representative EditableBatch exercising every field."""
    data = dict(
        id=id_ or slugify_title(title),
        title=title,
        description="A reference batch covering all schema fields.",
        round=3,
        round_title="Spectral Methods",
        round_theme="FFT-Galerkin micromechanics",
        status="active",
        source="user",
        papers=["paper-a", "paper-b"],
        nodes=[
            BatchNode(
                node_id="green-operator",
                title="Green Operator",
                description="periodic Green's operator",
                domain="micromechanics",
                tags=["fft", "spectral"],
                priority=1,
                status="planned",
            ),
            BatchNode(
                node_id="aitken",
                title="Aitken Acceleration",
                tags=["fixed-point"],
                priority=2,
                status="generated",
            ),
        ],
        notebook_id="nlm-xyz",
        zotsums_text="FFT collection: 10 papers",
        sources_text="AKMS_Sources/new/R3_B2_fft/",
        notes="Targets the strain compatibility tier first.",
        default_workflow_id="akms-node-extraction",
        prompt_profile_id="user:fft-profile",
        prompt_overrides={"akms.node_gen_skill": "user:akms.node_gen_skill.strict"},
        generation=BatchGenerationDefaults(
            default_output_dir="Outputs/R3_B2_fft",
            nlm_timeout=600.0,
            max_retries=3,
            output_format="markdown",
        ),
    )
    data.update(overrides)
    return EditableBatch(**data)


class TestBatchSchemaRoundTrip:
    """JSON round-trip is lossless on every field."""

    @pytest.mark.unit
    def test_batch_schema_round_trip(self):
        """Verifies a populated BatchStore dumps and reloads identically."""
        original = BatchStore(
            batches=[
                _fixture_batch(),
                _fixture_batch(
                    title="Plasticity Return Mapping",
                    id_="plasticity-return-mapping",
                    status="draft",
                    source="duplicated",
                    nodes=[],
                    papers=[],
                ),
            ]
        )
        text = original.to_json()
        reloaded = BatchStore.from_json(text)
        # Pydantic equality compares field by field — full identity.
        assert reloaded == original
        # And the JSON form is itself stable (no key reordering surprises).
        assert reloaded.to_json() == text

    @pytest.mark.unit
    def test_disk_round_trip_via_save_load(self, tmp_path: Path):
        """Verifies BatchStore.save -> BatchStore.load is the identity."""
        store = BatchStore(batches=[_fixture_batch()])
        path = store.save(tmp_path)
        assert path.exists()
        reloaded = BatchStore.load(tmp_path)
        assert reloaded == store
        # Schema version pinned at v1 on every write.
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["schema_version"] == SCHEMA_VERSION

    @pytest.mark.unit
    def test_load_missing_file_is_empty_store(self, tmp_path: Path):
        """First-run UX: a missing batches.json yields an empty store."""
        empty = BatchStore.load(tmp_path)
        assert empty.batches == []
        assert empty.schema_version == SCHEMA_VERSION

    @pytest.mark.unit
    def test_atomic_save_overwrites_in_place(self, tmp_path: Path):
        """Two consecutive saves to the same root replace the file content."""
        store_a = BatchStore(batches=[_fixture_batch(title="A")])
        store_b = BatchStore(batches=[_fixture_batch(title="B")])
        store_a.save(tmp_path)
        path_b = store_b.save(tmp_path)
        reloaded = BatchStore.load(tmp_path)
        assert reloaded == store_b
        # No leftover tempfiles.
        leftover = list(tmp_path.glob(".batches-*.json.tmp"))
        assert leftover == [], f"tempfile not cleaned up: {leftover}"
        assert path_b.name == "batches.json"


class TestSlugCollisionBehaviour:
    """Slug minting is deterministic and collision-safe."""

    @pytest.mark.unit
    def test_slugify_is_deterministic(self):
        """Same title produces the same slug regardless of call order/whitespace."""
        assert slugify_title("FFT Green Operators") == "fft-green-operators"
        assert slugify_title("  FFT Green Operators  ") == "fft-green-operators"
        assert slugify_title("FFT Green Operators!!!") == "fft-green-operators"

    @pytest.mark.unit
    def test_slugify_empty_returns_empty(self):
        """Empty / punctuation-only titles return '' so the caller can re-prompt."""
        assert slugify_title("") == ""
        assert slugify_title("   ") == ""
        assert slugify_title("!!!---") == ""

    @pytest.mark.unit
    def test_new_id_from_title_first_use_is_base_slug(self):
        store = BatchStore()
        assert store.new_id_from_title("FFT Green Operators") == "fft-green-operators"

    @pytest.mark.unit
    def test_new_id_from_title_collisions_get_numeric_suffix(self):
        store = BatchStore(
            batches=[
                _fixture_batch(title="FFT Green Operators"),
                _fixture_batch(
                    title="FFT Green Operators", id_="fft-green-operators-2"
                ),
            ]
        )
        # Next collision picks -3, not a random suffix.
        assert (
            store.new_id_from_title("FFT Green Operators")
            == "fft-green-operators-3"
        )

    @pytest.mark.unit
    def test_new_id_from_title_empty_title_raises(self):
        store = BatchStore()
        with pytest.raises(ValueError):
            store.new_id_from_title("   ")

    @pytest.mark.unit
    def test_add_rejects_duplicate_id(self):
        store = BatchStore(batches=[_fixture_batch(title="A")])
        dup = _fixture_batch(title="A")
        with pytest.raises(ValueError):
            store.add(dup)
