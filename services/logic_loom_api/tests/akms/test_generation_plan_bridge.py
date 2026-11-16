"""generation_plan.md <-> batches.json bridge.

Covers:
  A round-trip export -> import is a no-op on a fixture batch set.

Tier: integration. Uses a fixture under
``services/logic_loom_api/tests/fixtures/generation_plan_v1.md`` as the
golden reference; the round-trip must be model-equal after normalising
the workspace-only fields the legacy markdown cannot represent.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.batches import BatchNode, BatchStore, EditableBatch
from logic_loom_api.batches.compat import (
    GenerationPlanParseError,
    export_generation_plan,
    import_generation_plan,
    normalize_for_roundtrip,
    round_trip_via_markdown,
)

FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "generation_plan_v1.md"
)


def _seed_store() -> BatchStore:
    """Build a BatchStore whose contents the legacy markdown can fully
    represent (legacy-style ids R<n>_B<m>; size tags only; no workspace-
    only fields populated).
    """
    return BatchStore(
        batches=[
            EditableBatch(
                id="R1_B1",
                title="Tensor Operations",
                round=1,
                round_title="Tensor Algebra",
                round_theme="Mathematical language for all subsequent rounds.",
                source="imported",
                sources_text="Belytschko Ch. 2-3",
                zotsums_text="objectivity -> @SECONDORDERTENSOR",
                nodes=[
                    BatchNode(node_id="tensor-index-notation",
                              title="Index Notation & Einstein Convention",
                              tags=["small"]),
                    BatchNode(node_id="tensor-products-contractions",
                              title="Tensor Products & Contractions",
                              tags=["medium"]),
                    BatchNode(node_id="tensor-invariants",
                              title="Tensor Invariants",
                              tags=["medium"]),
                ],
            ),
            EditableBatch(
                id="R1_B2",
                title="Tensor Calculus",
                round=1,
                round_title="Tensor Algebra",
                round_theme="Mathematical language for all subsequent rounds.",
                source="imported",
                sources_text="Miehe et al. 2002, Holzapfel Ch. 2",
                nodes=[
                    BatchNode(node_id="tensor-derivatives-scalars",
                              title="Derivatives of Scalar Functions of Tensors",
                              tags=["medium"]),
                    BatchNode(node_id="tensor-spectral-decomposition",
                              title="Spectral Decomposition of Symmetric Tensors",
                              tags=["medium"]),
                ],
            ),
            EditableBatch(
                id="R3_B2",
                title="FFT Green Operators",
                round=3,
                round_title="Spectral Methods",
                round_theme="FFT-Galerkin micromechanics.",
                source="imported",
                sources_text="Moulinec-Suquet 1998",
                nodes=[
                    BatchNode(node_id="green-operator",
                              title="Periodic Green Operator",
                              tags=["medium"]),
                    BatchNode(node_id="aitken",
                              title="Aitken Acceleration",
                              tags=["small"]),
                ],
            ),
        ]
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "akms-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "prompt-library").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(workspace))
    import logic_loom_api.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.api)


class TestRoundTrip:
    """Export -> import is a no-op."""

    def test_fixture_imports_into_three_batches(self):
        """Sanity check: the golden fixture parses into the expected
        canonical set so other tests can lean on it.
        """
        text = FIXTURE_PATH.read_text(encoding="utf-8")
        parsed = import_generation_plan(text)
        assert [b.id for b in parsed] == ["R1_B1", "R1_B2", "R3_B2"]
        assert [b.round for b in parsed] == [1, 1, 3]
        # Round-2 of the fixture is implicitly absent — we only have R1 and R3.
        # Sources / zotsums carry through.
        assert parsed[0].sources_text == "Belytschko Ch. 2-3"
        assert parsed[0].zotsums_text == "objectivity -> @SECONDORDERTENSOR"
        # Nodes carry their size tag.
        assert parsed[0].nodes[0].tags == ["small"]

    def test_round_trip_export_import_is_noop(self):
        """Model-equal after normalising workspace-only fields the legacy
        markdown cannot carry.
        """
        store = _seed_store()
        re_imported = round_trip_via_markdown(store)
        assert len(re_imported) == len(store.batches)
        for original, returned in zip(store.batches, re_imported):
            assert normalize_for_roundtrip(original) == normalize_for_roundtrip(
                returned
            )

    def test_export_text_parses_into_same_batches(self):
        """Exported markdown re-imports into the same logical set."""
        store = _seed_store()
        text = export_generation_plan(store)
        reparsed = import_generation_plan(text)
        assert [b.id for b in reparsed] == [b.id for b in store.batches]
        assert [b.title for b in reparsed] == [b.title for b in store.batches]
        for orig, ret in zip(store.batches, reparsed):
            assert [n.node_id for n in ret.nodes] == [
                n.node_id for n in orig.nodes
            ]

    def test_export_skips_non_legacy_ids(self):
        """Workspace-slug ids (e.g. 'fft-green-operators') don't make it
        into the legacy export — they would not round-trip through the
        upstream parser.
        """
        store = BatchStore(
            batches=[
                EditableBatch(
                    id="custom-slug", title="Custom", round=1, round_title="R1"
                ),
                EditableBatch(id="R1_B1", title="Legacy", round=1, round_title="R1"),
            ]
        )
        text = export_generation_plan(store)
        # Only the R1_B1 batch is rendered.
        assert "## R1_B1" in text
        assert "## custom-slug" not in text

    def test_parser_raises_on_missing_node_id_column(self):
        """The one edge case the spec calls out as clear-error-not-guess."""
        text = """# Round 1: Bad

## R1_B1 — Bad table (1 nodes)

| Foo | Bar |
|-----|-----|
| a | b |
"""
        with pytest.raises(GenerationPlanParseError):
            import_generation_plan(text)


class TestImportEndpoint:
    """POST /api/batches/import-plan persists the parsed batches."""

    def test_import_preview_returns_parsed_without_writing(
        self, client: TestClient
    ):
        text = FIXTURE_PATH.read_text(encoding="utf-8")
        r = client.post(
            "/api/batches/import-plan", json={"text": text, "mode": "preview"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "preview"
        assert [b["id"] for b in body["parsed"]] == ["R1_B1", "R1_B2", "R3_B2"]
        assert body["written"] == []
        # No write happened: list is empty.
        assert client.get("/api/batches").json()["batches"] == []

    def test_import_merge_writes_new_skips_existing(self, client: TestClient):
        text = FIXTURE_PATH.read_text(encoding="utf-8")
        # First merge: everything is new.
        r = client.post(
            "/api/batches/import-plan", json={"text": text, "mode": "merge"}
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body["written"]) == {"R1_B1", "R1_B2", "R3_B2"}
        assert body["skipped"] == []
        # Second merge: everything is already present.
        r = client.post(
            "/api/batches/import-plan", json={"text": text, "mode": "merge"}
        )
        assert r.status_code == 200
        assert r.json()["written"] == []
        assert set(r.json()["skipped"]) == {"R1_B1", "R1_B2", "R3_B2"}

    def test_import_replace_overwrites(self, client: TestClient):
        text = FIXTURE_PATH.read_text(encoding="utf-8")
        client.post(
            "/api/batches/import-plan", json={"text": text, "mode": "merge"}
        )
        # Mutate one batch through the API, then re-import in replace mode.
        client.patch("/api/batches/R1_B1", json={"description": "user note"})
        # Replace mode requires a preview_token from a recent preview.
        prev = client.post(
            "/api/batches/import-plan", json={"text": text, "mode": "preview"}
        )
        assert prev.status_code == 200
        token = prev.json()["preview_token"]
        r = client.post(
            "/api/batches/import-plan",
            json={"text": text, "mode": "replace", "preview_token": token},
        )
        assert r.status_code == 200
        # Replace wipes the user note (the legacy markdown can't carry it).
        got = client.get("/api/batches/R1_B1").json()
        assert got["description"] == ""

    def test_import_endpoint_persists_batch(self, client: TestClient):
        text = FIXTURE_PATH.read_text(encoding="utf-8")
        client.post(
            "/api/batches/import-plan", json={"text": text, "mode": "merge"}
        )
        r = client.get("/api/batches/R3_B2")
        assert r.status_code == 200
        b = r.json()
        assert b["title"] == "FFT Green Operators"
        assert [n["node_id"] for n in b["nodes"]] == ["green-operator", "aitken"]

    def test_import_unknown_mode_400(self, client: TestClient):
        r = client.post(
            "/api/batches/import-plan",
            json={"text": "irrelevant", "mode": "annihilate"},
        )
        assert r.status_code == 400

    def test_import_bad_table_400(self, client: TestClient):
        bad = "# Round 1: X\n\n## R1_B1 — Y (1 nodes)\n\n| Foo |\n|-----|\n| a |\n"
        r = client.post(
            "/api/batches/import-plan", json={"text": bad, "mode": "preview"}
        )
        assert r.status_code == 400

    def test_export_endpoint_returns_text(self, client: TestClient):
        # Seed something parseable then export.
        text = FIXTURE_PATH.read_text(encoding="utf-8")
        client.post(
            "/api/batches/import-plan", json={"text": text, "mode": "merge"}
        )
        r = client.get("/api/batches/export-plan")
        assert r.status_code == 200
        body = r.json()["text"]
        assert "## R1_B1" in body
        assert "## R3_B2" in body
