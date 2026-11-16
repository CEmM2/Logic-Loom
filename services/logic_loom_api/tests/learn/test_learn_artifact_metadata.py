"""Tests for artifact metadata (notebook/assessment/HTML/generated sections).

Covers: the artifact endpoint returns correct metadata per artifact family.

Strategy:
  - Real TestClient + graph_slice compile (mirrors the
    test_learn_compile_mode_validation.py helpers).
  - Discover available exporters via GET /api/learn/exporters.
  - Compile with all available exporters in one shot.
  - Assert that each export dict carries {path, type, metadata} and that
    each metadata dict exposes the family-specific keys.
  - Skip a family whose exporter is not available (no hard-fail).
  - Unit-level helper: test _artifact_metadata logic directly via
    a synthetic CompileResult-like object.

Reuses _fixture_graph_slice_payload() and _compile_body() from
test_learn_compile_mode_validation.py.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None


# ---------------------------------------------------------------------------
# Shared fixture (mirrors test_learn_compile_mode_validation.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient with tmp-path settings so no real filesystem state is touched."""
    cfg_path = tmp_path / "config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg_path),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        },
    ):
        from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

        reload_settings()
        from logic_loom_api.main import api  # type: ignore[import-not-found]

        with TestClient(api) as c:
            yield c
        reload_settings()


# ---------------------------------------------------------------------------
# Helpers (copied from test_learn_compile_mode_validation.py to keep this file
# self-contained)
# ---------------------------------------------------------------------------


def _fixture_graph_slice_payload() -> dict:
    """A minimal in-memory GraphSlice payload that compiles successfully."""
    return {
        "nodes": [
            {
                "node_id": "concept-a",
                "title": "Concept A",
                "markdown": "# Concept A\n\nIntro body.\n\n## Derivation\n\nstep.",
            },
            {
                "node_id": "concept-b",
                "title": "Concept B",
                "markdown": "# Concept B\n\nFollow-up body.",
            },
        ],
        "edges": [
            {
                "edge_id": "concept-a__requires__concept-b",
                "from": "concept-b",
                "to": "concept-a",
                "type": "requires",
            }
        ],
        "metadata": {},
    }


def _compile_body(generation_option: str, **overrides) -> dict:
    """A minimal valid CompileRequest body (GraphSlice path) for *generation_option*."""
    body: dict = {
        "generation_option": generation_option,
        "topic": "j2 return mapping",
        "goal": "Understand the j2 return-mapping algorithm",
        "graph_slice": _fixture_graph_slice_payload(),
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Unit-level helper tests (no network, no filesystem)
# ---------------------------------------------------------------------------


class TestArtifactMetadataHelper:
    """Unit tests for _artifact_metadata — verify family dispatch logic."""

    def _make_result(self, generation_option: str = "deterministic_outline") -> Any:
        """Synthetic CompileResult-like object.

        Uses the REAL LearningSourcePacket shape:
          result.packet.request.generation_option
        (NOT result.packet.generation_option — that attribute does not exist
         on a real packet; generation_option lives on the nested
         LearningRequestInfo sub-model at packet.request).
        """
        r = MagicMock()
        r.packet = MagicMock()
        r.packet.request = MagicMock()
        r.packet.request.generation_option = generation_option
        r.warnings = []
        r.packet_path = None
        r.export_paths = []
        return r

    def test_packet_type_returns_empty_metadata(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        p = tmp_path / "abc123.json"
        p.touch()
        result = self._make_result()
        meta = _artifact_metadata(p, "packet", result)
        assert meta == {}, f"packet must return empty metadata, got {meta}"

    def test_zip_returns_empty_metadata(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        p = tmp_path / "bundle.zip"
        p.touch()
        result = self._make_result()
        meta = _artifact_metadata(p, "zip", result)
        assert meta == {}, f"zip must return empty metadata, got {meta}"

    def test_html_metadata_has_static_true(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        p = tmp_path / "lesson.html"
        p.write_text("<html><body>Hello</body></html>")
        result = self._make_result()
        meta = _artifact_metadata(p, "html", result)
        assert meta.get("static") is True, f"html.static must be True, got {meta}"
        assert "size_bytes" in meta, f"html must carry size_bytes, got {meta}"
        assert isinstance(meta["size_bytes"], int) and meta["size_bytes"] > 0

    def test_html_generated_content_false_for_deterministic(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        p = tmp_path / "lesson.html"
        p.write_text("<html/>")
        result = self._make_result("deterministic_outline")
        meta = _artifact_metadata(p, "html", result)
        assert meta.get("generated_content") is False

    def test_html_generated_content_true_for_llm_expanded(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        p = tmp_path / "lesson.html"
        p.write_text("<html/>")
        result = self._make_result("llm_expanded")
        meta = _artifact_metadata(p, "html", result)
        assert meta.get("generated_content") is True

    def test_markdown_has_generated_content(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        p = tmp_path / "lesson.md"
        p.write_text("# Lesson\n\nContent.")
        result = self._make_result("deterministic_outline")
        meta = _artifact_metadata(p, "md", result)
        assert "generated_content" in meta, f"markdown must carry generated_content, got {meta}"

    def test_assessment_json_gets_assessment_family(self, tmp_path: Path) -> None:
        """assessment.json is a real public artifact of the assessment exporter,
        so it must receive assessment-family metadata (has_public/has_answer_key)."""
        from logic_loom_api.routes.learn import _artifact_metadata

        (tmp_path / "assessment.md").write_text("# Assessment")
        (tmp_path / "rubric.md").write_text("# Answer key")
        p = tmp_path / "assessment.json"
        p.write_text("{}")
        meta = _artifact_metadata(p, "json", self._make_result())
        assert meta.get("has_public") is True, f"assessment.json must be tagged: {meta}"
        assert meta.get("has_answer_key") is True, f"rubric presence must be detected: {meta}"

    def test_unrelated_assessment_substring_not_tagged(self, tmp_path: Path) -> None:
        """A file that merely CONTAINS 'assessment' in its name (not one of the
        three exporter filenames) must NOT receive assessment-family metadata —
        guards the fix that replaced the loose `"assessment" in name`
        substring with an exact-filename match."""
        from logic_loom_api.routes.learn import _artifact_metadata

        p = tmp_path / "self_assessment_notes.md"
        p.write_text("# notes")
        meta = _artifact_metadata(p, "md", self._make_result())
        assert "has_public" not in meta, f"non-assessment file mis-tagged: {meta}"
        assert "has_answer_key" not in meta, f"non-assessment file mis-tagged: {meta}"
        # Falls through to the markdown family instead.
        assert "generated_content" in meta, f"should be markdown family: {meta}"

    def test_notebook_metadata_keys(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        nb = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {"cell_type": "markdown", "source": "# Title", "metadata": {}},
                {
                    "cell_type": "code",
                    "source": "x = 1",
                    "metadata": {},
                    "execution_count": None,
                    "outputs": [],
                },
            ],
        }
        p = tmp_path / "lesson.ipynb"
        p.write_text(json.dumps(nb))
        result = self._make_result()
        meta = _artifact_metadata(p, "ipynb", result)
        assert "cell_count" in meta, f"notebook must carry cell_count, got {meta}"
        assert "executed" in meta, f"notebook must carry executed, got {meta}"
        assert "generated_content" in meta, f"notebook must carry generated_content, got {meta}"
        assert meta["cell_count"] == 2
        assert meta["executed"] is False  # execution_count is None

    def test_notebook_executed_true_when_execution_count_set(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        nb = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {
                    "cell_type": "code",
                    "source": "x = 1",
                    "metadata": {},
                    "execution_count": 1,
                    "outputs": [{"output_type": "execute_result", "data": {}, "metadata": {}, "execution_count": 1}],
                }
            ],
        }
        p = tmp_path / "lesson.ipynb"
        p.write_text(json.dumps(nb))
        result = self._make_result()
        meta = _artifact_metadata(p, "ipynb", result)
        assert meta["executed"] is True
        assert meta["cell_count"] == 1

    def test_assessment_has_public_and_answer_key(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        # Both files present
        (tmp_path / "assessment.md").write_text("# Assessment\n\nQ1.")
        (tmp_path / "rubric.md").write_text("# Rubric (hidden)")
        p = tmp_path / "assessment.md"
        result = self._make_result()
        meta = _artifact_metadata(p, "md", result)
        assert "has_public" in meta, f"assessment must carry has_public, got {meta}"
        assert "has_answer_key" in meta, f"assessment must carry has_answer_key, got {meta}"
        assert meta["has_public"] is True
        assert meta["has_answer_key"] is True

    def test_assessment_answer_key_false_when_rubric_absent(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        (tmp_path / "assessment.md").write_text("# Assessment\n\nQ1.")
        # rubric.md NOT created
        p = tmp_path / "assessment.md"
        result = self._make_result()
        meta = _artifact_metadata(p, "md", result)
        assert meta["has_public"] is True
        assert meta["has_answer_key"] is False

    def test_rubric_md_also_triggers_assessment_family(self, tmp_path: Path) -> None:
        from logic_loom_api.routes.learn import _artifact_metadata

        (tmp_path / "assessment.md").write_text("# Assessment")
        rubric = tmp_path / "rubric.md"
        rubric.write_text("# Rubric (hidden)")
        result = self._make_result()
        meta = _artifact_metadata(rubric, "md", result)
        assert "has_public" in meta
        assert "has_answer_key" in meta

    def test_serialize_exports_adds_metadata_key(self, tmp_path: Path) -> None:
        """_serialize_exports emits {path, type, metadata} for every export."""
        from logic_loom_api.routes.learn import _serialize_exports

        md_file = tmp_path / "lesson.md"
        md_file.write_text("# Lesson")

        result = MagicMock()
        result.packet_path = None
        result.export_paths = [md_file]
        result.packet = MagicMock()
        result.packet.request = MagicMock()
        result.packet.request.generation_option = "deterministic_outline"
        result.warnings = []

        exports = _serialize_exports(result)
        assert len(exports) == 1
        e = exports[0]
        assert "path" in e, "path must be present (backward compat)"
        assert "type" in e, "type must be present (backward compat)"
        assert "metadata" in e, "metadata must be added to every export"
        assert isinstance(e["metadata"], dict)


# ---------------------------------------------------------------------------
# Integration tests: compile + assert metadata on real exports
# ---------------------------------------------------------------------------


class TestArtifactMetadataPerFamily:
    """Integration tests: artifact metadata per family."""

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_metadata_present_per_artifact_family(self, client: TestClient) -> None:
        """Verifies: the artifact response carries type-specific metadata for each
        artifact family (notebook, assessment, HTML, generated sections): the
        artifact endpoint returns correct metadata per artifact family.

        Passes when: each emitted family exposes its expected metadata keys.
        Families whose exporter is not available are silently skipped (not
        hard-failed), so the test is always runnable with the core extras only.
        """
        # 1. Discover available exporters.
        # The /exporters endpoint returns a DICT: {"exporters": [...], "akms_learn_available": bool}
        # Each item in the list has {id, label, status} where id is an *_export capability name.
        # KNOWN_EXPORTERS (akms_learn.exporters) are the BARE names the compiler accepts:
        #   ("markdown", "bundle", "notebook", "assessment", "html")
        # Map: strip "_export" suffix; quiz_export -> assessment (no such bare name).
        _EXPORT_ID_TO_BARE: dict[str, str] = {
            "markdown_export": "markdown",
            "bundle_export": "bundle",
            "notebook_export": "notebook",
            "html_export": "html",
            "quiz_export": "assessment",
            "assessment_export": "assessment",
        }
        # KNOWN_EXPORTERS from akms_learn — the compiler only accepts these bare names.
        _KNOWN_BARE = {"markdown", "bundle", "notebook", "assessment", "html"}

        r_exp = client.get("/api/learn/exporters")
        assert r_exp.status_code == 200, f"exporters endpoint must return 200; got {r_exp.text}"
        exporters_payload = r_exp.json()
        # Response is a dict: {"exporters": [...], "akms_learn_available": bool}
        assert isinstance(exporters_payload, dict), (
            f"/exporters must return a dict, got {type(exporters_payload)}: {exporters_payload}"
        )
        exporter_items: list[dict] = exporters_payload.get("exporters", [])
        assert isinstance(exporter_items, list), (
            f"exporters_payload['exporters'] must be a list, got {type(exporter_items)}"
        )

        # Build bare-name list of available exporters (deduplicated, KNOWN_EXPORTERS only).
        available_bare_exporters: list[str] = []
        seen_bare: set[str] = set()
        for item in exporter_items:
            cap_id: str = item.get("id", "")
            status: str = item.get("status", "unavailable")
            if status != "available":
                continue
            bare = _EXPORT_ID_TO_BARE.get(cap_id) or (
                cap_id.removesuffix("_export") if cap_id.endswith("_export") else cap_id
            )
            if bare in _KNOWN_BARE and bare not in seen_bare:
                available_bare_exporters.append(bare)
                seen_bare.add(bare)

        if not available_bare_exporters:
            pytest.skip("No available exporters — cannot test artifact metadata")

        # Always request notebook + html + markdown + bundle if available (the rich families).
        # These are the families whose metadata keys are tested below.
        rich_families_requested: set[str] = set()
        for name in ("notebook", "html", "markdown", "bundle"):
            if name in seen_bare:
                rich_families_requested.add(name)

        # 2. Pick a base generation mode that is available.
        from logic_loom_api.adapters import learn_adapter

        modes = learn_adapter.get_generation_mode_capabilities()
        available_modes = [m["capability"] for m in modes if m["status"] == "available"]
        if not available_modes:
            pytest.skip("No generation modes available")

        base_mode = available_modes[0]

        # 3. Compile with all available exporters (bare names for akms_learn).
        body = _compile_body(base_mode, exporters=available_bare_exporters)
        r = client.post("/api/learn/compile", json=body)
        assert r.status_code == 200, (
            f"Compile with available exporters must return 200; got {r.status_code}: {r.text}"
        )

        data = r.json()
        exports: list[dict] = data.get("exports", [])
        assert isinstance(exports, list), "'exports' must be a list"

        # 4. Assert every export carries {path, type, metadata}.
        for e in exports:
            assert "path" in e, f"export must carry 'path': {e}"
            assert "type" in e, f"export must carry 'type': {e}"
            assert "metadata" in e, (
                f"export must carry 'metadata' key; got keys {set(e.keys())} for export {e}"
            )
            assert isinstance(e["metadata"], dict), (
                f"export 'metadata' must be a dict; got {type(e['metadata'])} for export {e}"
            )

        # 5. For each emitted family, verify the expected metadata keys.
        #    We classify by the same rules as _artifact_metadata.
        families_seen: set[str] = set()

        for e in exports:
            path_str: str = e["path"]
            p = Path(path_str)
            suffix = p.suffix.lower()
            name = p.name.lower()
            meta = e["metadata"]

            if e["type"] == "packet":
                families_seen.add("packet")
                # packet: no mandatory keys; just an empty dict is fine
                continue

            if suffix == ".ipynb":
                families_seen.add("notebook")
                assert "cell_count" in meta, (
                    f"notebook export must carry 'cell_count'; got {meta}"
                )
                assert "executed" in meta, (
                    f"notebook export must carry 'executed'; got {meta}"
                )
                assert "generated_content" in meta, (
                    f"notebook export must carry 'generated_content'; got {meta}"
                )
                assert isinstance(meta["cell_count"], int) and meta["cell_count"] >= 0
                assert isinstance(meta["executed"], bool)

            elif suffix == ".html":
                families_seen.add("html")
                assert meta.get("static") is True, (
                    f"html export must have static=True; got {meta}"
                )
                assert "size_bytes" in meta, (
                    f"html export must carry 'size_bytes'; got {meta}"
                )
                assert "generated_content" in meta, (
                    f"html export must carry 'generated_content'; got {meta}"
                )

            elif suffix == ".zip":
                families_seen.add("bundle")
                # bundle metadata is minimal by design (domain-pack metadata lives on /status)
                assert isinstance(meta, dict)

            elif name == "assessment.md" or name == "rubric.md" or "assessment" in name:
                families_seen.add("assessment")
                # assessment family: assert shape only IF the artifact actually appears;
                # a generic graph_slice fixture may not produce assessment items.
                assert "has_public" in meta, (
                    f"assessment export must carry 'has_public'; got {meta}"
                )
                assert "has_answer_key" in meta, (
                    f"assessment export must carry 'has_answer_key'; got {meta}"
                )
                assert "generated_content" in meta, (
                    f"assessment export must carry 'generated_content'; got {meta}"
                )
                assert isinstance(meta["has_public"], bool)
                assert isinstance(meta["has_answer_key"], bool)

            elif suffix == ".md":
                families_seen.add("markdown")
                assert "generated_content" in meta, (
                    f"markdown export must carry 'generated_content'; got {meta}"
                )

        # 6. Assert that the rich families we actually requested were seen in the output.
        # notebook and html are the key families with real metadata keys; if they were
        # requested (available) they MUST appear in the exports.
        for rich_family in ("notebook", "html"):
            if rich_family in rich_families_requested:
                assert rich_family in families_seen, (
                    f"Requested exporter '{rich_family}' but its artifacts were not found in exports. "
                    f"families_seen={families_seen}, exports={exports}"
                )

        # 7. At minimum, the core markdown family must be present (always available).
        assert "markdown" in families_seen or "packet" in families_seen, (
            f"Expected at least markdown or packet in exports; families seen: {families_seen}. "
            f"exports: {exports}"
        )

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_exports_backward_compatible_path_and_type(self, client: TestClient) -> None:
        """Verifies backward compatibility: exports still carry 'path' + 'type' keys
        (the contract_shape test in test_learn_backend_suite.py must keep passing).
        The additive 'metadata' key must not break the existing shape.
        """
        from logic_loom_api.adapters import learn_adapter

        modes = learn_adapter.get_generation_mode_capabilities()
        available_modes = [m["capability"] for m in modes if m["status"] == "available"]
        if not available_modes:
            pytest.skip("No generation modes available")

        body = _compile_body(available_modes[0])
        r = client.post("/api/learn/compile", json=body)
        assert r.status_code == 200, f"Expected 200; got {r.status_code}: {r.text}"

        for e in r.json().get("exports", []):
            assert "path" in e, f"'path' must still be present (backward compat): {e}"
            assert "type" in e, f"'type' must still be present (backward compat): {e}"
            assert "metadata" in e, f"'metadata' must be present: {e}"
