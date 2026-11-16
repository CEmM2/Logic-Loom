"""Tests for structured compile warnings (generated content / skipped prereqs / missing deps).

Covers: warnings carry category + message; missing optional deps are reported.

Strategy:
  - Unit tests on ``_serialize_warnings`` via synthetic CompileResult-like objects:
      (a) generated-content warning synthesized for llm_expanded option.
      (b) missing-dep warning surfaced for unavailable requested exporter.
      (c) existing akms_learn warnings flow through with a category key.
  - Integration tests via a real compile:
      (a) every warning dict in a real compile response carries both ``category``
          and ``message`` (additive shape; does not break the original warning contract).
      (b) requesting an unavailable exporter surfaces a missing_optional_dep warning.

Note on llm_expanded:
  ``llm_expanded`` is unavailable in this env (no LLM provider), so the
  generated-content synthesis path (``gen_opt == "llm_expanded"``) cannot be
  exercised via a real compile.  It is covered by a focused unit test on
  _serialize_warnings with a synthetic result.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None


# ---------------------------------------------------------------------------
# Shared fixture (mirrors test_learn_compile_mode_validation.py / test_learn_artifact_metadata.py)
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
# Helpers (copied from test_learn_compile_mode_validation.py /
# test_learn_artifact_metadata.py to keep file self-contained)
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
# Helper: build a synthetic CompileResult-like object
# ---------------------------------------------------------------------------


def _make_synthetic_result(
    generation_option: str = "deterministic_outline",
    warnings: list[Any] | None = None,
    unavailable_capabilities: list[str] | None = None,
) -> Any:
    """Return a MagicMock that quacks like a CompileResult.

    Replicates the real packet shape:
      result.packet.request.generation_option  (nested — NOT result.packet.generation_option)
    """
    r = MagicMock()
    r.packet = MagicMock()
    r.packet.request = MagicMock()
    r.packet.request.generation_option = generation_option
    r.packet_path = None
    r.export_paths = []
    r.warnings = warnings if warnings is not None else []
    r.unavailable_capabilities = unavailable_capabilities if unavailable_capabilities is not None else []
    return r


def _make_learning_warning(
    code: str,
    message: str = "A warning message.",
    severity: str = "warning",
    source_ref: str | None = None,
) -> Any:
    """Return a MagicMock that quacks like a LearningWarning."""
    w = MagicMock()
    w.code = code
    w.message = message
    w.severity = severity
    w.source_ref = source_ref
    return w


# ---------------------------------------------------------------------------
# Unit tests for _serialize_warnings
# ---------------------------------------------------------------------------


class TestSerializeWarningsUnit:
    """Pure unit tests for _serialize_warnings — no network, no filesystem."""

    def test_every_warning_carries_category_and_message(self) -> None:
        """Every serialized warning dict must carry both ``category`` and ``message``."""
        from logic_loom_api.routes.learn import _serialize_warnings

        w = _make_learning_warning(code="missing_section", message="A section is missing.")
        result = _make_synthetic_result(warnings=[w])
        out = _serialize_warnings(result)
        assert len(out) == 1
        entry = out[0]
        assert "category" in entry, f"category key missing from: {entry}"
        assert "message" in entry, f"message key missing from: {entry}"
        assert entry["message"] == "A section is missing."

    def test_legacy_keys_preserved(self) -> None:
        """severity / code / message / source_ref must all still be present (backward compat)."""
        from logic_loom_api.routes.learn import _serialize_warnings

        w = _make_learning_warning(
            code="dangling_reference",
            message="Edge references unknown node.",
            severity="warning",
            source_ref="edge-1",
        )
        result = _make_synthetic_result(warnings=[w])
        out = _serialize_warnings(result)
        entry = out[0]
        for key in ("severity", "code", "message", "source_ref"):
            assert key in entry, f"legacy key '{key}' missing from: {entry}"
        assert entry["severity"] == "warning"
        assert entry["code"] == "dangling_reference"
        assert entry["source_ref"] == "edge-1"

    def test_source_quality_category_for_missing_section(self) -> None:
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_SOURCE_QUALITY

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("missing_section")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_SOURCE_QUALITY

    def test_source_quality_category_for_dangling_reference(self) -> None:
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_SOURCE_QUALITY

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("dangling_reference")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_SOURCE_QUALITY

    def test_source_quality_category_for_code_mirror_missing_source_path(self) -> None:
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_SOURCE_QUALITY

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("code_mirror_missing_source_path")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_SOURCE_QUALITY

    def test_skipped_prerequisite_category_for_thin_pitfall_content(self) -> None:
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_SKIPPED_PREREQUISITE

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("thin_pitfall_content")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_SKIPPED_PREREQUISITE

    def test_skipped_prerequisite_category_for_pedagogical_section_missing(self) -> None:
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_SKIPPED_PREREQUISITE

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("pedagogical_section_missing")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_SKIPPED_PREREQUISITE

    # --- Category re-mapping fixes ---

    def test_llm_citation_outside_packet_maps_to_generated_content(self) -> None:
        """llm_citation_outside_packet is a rejected LLM-generated section — must be
        generated_content, not skipped_prerequisite.
        akms_learn message: "GeneratedSection ... cites node id(s) not present in
        packet.nodes; section rejected and NOT attached to LSP."
        """
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_GENERATED_CONTENT

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("llm_citation_outside_packet")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_GENERATED_CONTENT, (
            f"llm_citation_outside_packet must map to generated_content; "
            f"got: {out[0]['category']}"
        )

    def test_invalid_reading_priority_maps_to_source_quality(self) -> None:
        """invalid_reading_priority is malformed graph data (coerced) — must be
        source_quality, not skipped_prerequisite.
        akms_learn message: "Node has non-numeric reading_priority; coerced to +infinity
        (sorts last)."
        """
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_SOURCE_QUALITY

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("invalid_reading_priority")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_SOURCE_QUALITY, (
            f"invalid_reading_priority must map to source_quality; "
            f"got: {out[0]['category']}"
        )

    def test_adaptive_learner_claim_unmatched_maps_to_source_quality(self) -> None:
        """adaptive_learner_claim_unmatched is an input/graph mismatch — must be
        source_quality, not skipped_prerequisite.
        akms_learn message: "Learner claim does not match any graph tag or node id."
        """
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_SOURCE_QUALITY

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("adaptive_learner_claim_unmatched")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_SOURCE_QUALITY, (
            f"adaptive_learner_claim_unmatched must map to source_quality; "
            f"got: {out[0]['category']}"
        )

    def test_missing_teaching_section_maps_to_source_quality(self) -> None:
        """missing_teaching_section is the structural twin of missing_section
        (node lacks a teaching section) — must be source_quality, not
        skipped_prerequisite.
        akms_learn message: "Node missing teaching section."
        """
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_SOURCE_QUALITY

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("missing_teaching_section")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_SOURCE_QUALITY, (
            f"missing_teaching_section must map to source_quality; "
            f"got: {out[0]['category']}"
        )

    def test_missing_optional_dep_for_exporter_unavailable(self) -> None:
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_MISSING_OPTIONAL_DEP

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("exporter_unavailable", source_ref="notebook")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_MISSING_OPTIONAL_DEP

    def test_missing_optional_dep_for_llm_provider_unavailable(self) -> None:
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_MISSING_OPTIONAL_DEP

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("llm_provider_unavailable")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_MISSING_OPTIONAL_DEP

    def test_generic_category_for_unknown_code(self) -> None:
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_GENERIC

        result = _make_synthetic_result(
            warnings=[_make_learning_warning("totally_unknown_code_xyz")]
        )
        out = _serialize_warnings(result)
        assert out[0]["category"] == _WARN_CAT_GENERIC, (
            f"Unknown code should map to generic, got: {out[0]['category']}"
        )

    def test_unavailable_capabilities_map_to_missing_optional_dep(self) -> None:
        """unavailable_capabilities list → missing_optional_dep category."""
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_MISSING_OPTIONAL_DEP

        result = _make_synthetic_result(unavailable_capabilities=["some_capability"])
        out = _serialize_warnings(result)
        assert len(out) == 1
        assert out[0]["code"] == "unavailable_capability"
        assert out[0]["category"] == _WARN_CAT_MISSING_OPTIONAL_DEP
        assert "category" in out[0] and "message" in out[0]

    def test_generated_content_warning_synthesized_for_llm_expanded(self) -> None:
        """Verifies: a result with generation_option == 'llm_expanded' synthesizes
        a generated_content category warning. Requirement: generated-content warning surfaced.
        """
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_GENERATED_CONTENT

        result = _make_synthetic_result(generation_option="llm_expanded")
        out = _serialize_warnings(result)

        # There must be at least one generated_content warning.
        gen_warnings = [w for w in out if w.get("category") == _WARN_CAT_GENERATED_CONTENT]
        assert len(gen_warnings) >= 1, (
            f"Expected a generated_content warning for llm_expanded; got: {out}"
        )
        gw = gen_warnings[0]
        assert "message" in gw, f"generated_content warning must carry 'message': {gw}"
        assert "category" in gw, f"generated_content warning must carry 'category': {gw}"
        assert gw["category"] == _WARN_CAT_GENERATED_CONTENT
        assert gw["message"], "generated_content warning message must be non-empty"

    def test_no_generated_content_warning_for_deterministic_outline(self) -> None:
        """Non-LLM mode must NOT produce a generated_content warning."""
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_GENERATED_CONTENT

        result = _make_synthetic_result(generation_option="deterministic_outline")
        out = _serialize_warnings(result)
        gen_warnings = [w for w in out if w.get("category") == _WARN_CAT_GENERATED_CONTENT]
        assert len(gen_warnings) == 0, (
            f"deterministic_outline must not produce a generated_content warning; got: {gen_warnings}"
        )

    def test_missing_dep_warning_for_unavailable_requested_exporter(self) -> None:
        """Verifies: requesting an exporter whose capability status is unavailable
        surfaces a missing_optional_dep warning naming the exporter.
        Requirement: missing optional deps reported.

        Uses a mock for get_capabilities_with_status to avoid real-env dependency.
        """
        from logic_loom_api.routes import learn as learn_route_module
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_MISSING_OPTIONAL_DEP

        # Simulate: "my_fancy_exporter_export" is unavailable
        fake_caps = [
            {"capability": "my_fancy_exporter_export", "status": "unavailable"},
            {"capability": "markdown_export", "status": "available"},
        ]

        result = _make_synthetic_result()
        with patch.object(
            learn_route_module.learn_adapter,
            "get_capabilities_with_status",
            return_value=fake_caps,
        ):
            out = _serialize_warnings(result, requested_exporters=["my_fancy_exporter"])

        dep_warnings = [w for w in out if w.get("category") == _WARN_CAT_MISSING_OPTIONAL_DEP]
        assert len(dep_warnings) >= 1, (
            f"Expected a missing_optional_dep warning for unavailable exporter; got: {out}"
        )
        dw = dep_warnings[0]
        assert "message" in dw, f"missing_optional_dep warning must carry 'message': {dw}"
        assert "my_fancy_exporter" in dw["message"], (
            f"missing_optional_dep warning message must name the exporter; got: {dw['message']}"
        )
        assert "category" in dw

    def test_available_exporter_does_not_produce_missing_dep_warning(self) -> None:
        """Available exporters must not produce spurious missing-dep warnings."""
        from logic_loom_api.routes import learn as learn_route_module
        from logic_loom_api.routes.learn import _serialize_warnings, _WARN_CAT_MISSING_OPTIONAL_DEP

        fake_caps = [
            {"capability": "markdown_export", "status": "available"},
        ]

        result = _make_synthetic_result()
        with patch.object(
            learn_route_module.learn_adapter,
            "get_capabilities_with_status",
            return_value=fake_caps,
        ):
            out = _serialize_warnings(result, requested_exporters=["markdown"])

        dep_warnings = [w for w in out if w.get("category") == _WARN_CAT_MISSING_OPTIONAL_DEP]
        assert len(dep_warnings) == 0, (
            f"Available exporter must not produce missing_optional_dep warning; got: {dep_warnings}"
        )

    def test_no_warnings_when_result_is_clean(self) -> None:
        """A result with no warnings + no unavailable caps produces an empty list."""
        from logic_loom_api.routes.learn import _serialize_warnings

        result = _make_synthetic_result(generation_option="deterministic_outline")
        out = _serialize_warnings(result)
        assert out == [], f"Clean result must produce no warnings; got: {out}"


# ---------------------------------------------------------------------------
# Integration tests: compile via TestClient + assert warning shape
# ---------------------------------------------------------------------------


class TestStructuredWarningsIntegration:
    """Integration tests: structured warnings model + propagation."""

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_generated_content_warning_surfaced(self, client: TestClient) -> None:
        """Verifies: every warning dict in a real compile carries ``category`` + ``message``.

        Since llm_expanded is unavailable in this env (no LLM provider), we
        cannot trigger the generated_content synthesis path through a real
        compile. Instead this integration test asserts the ADDITIVE shape
        guarantee: every warning (if any) carries both 'category' and 'message'.
        The unit test above covers the generated_content synthesis directly.

        Requirement: warnings carry category + message.
        """
        from logic_loom_api.adapters import learn_adapter

        modes = learn_adapter.get_generation_mode_capabilities()
        available_modes = [m["capability"] for m in modes if m["status"] == "available"]
        if not available_modes:
            pytest.skip("No generation modes available")

        body = _compile_body(available_modes[0])
        r = client.post("/api/learn/compile", json=body)
        assert r.status_code == 200, f"Expected 200; got {r.status_code}: {r.text}"

        data = r.json()
        warnings = data.get("warnings")
        assert isinstance(warnings, list), "'warnings' must be a list"

        # Every warning dict must carry both 'category' and 'message' (AC).
        for w in warnings:
            assert "category" in w, (
                f"every warning must carry 'category'; "
                f"got keys {set(w.keys())} for warning {w}"
            )
            assert "message" in w, (
                f"every warning must carry 'message'; "
                f"got keys {set(w.keys())} for warning {w}"
            )
            assert isinstance(w["category"], str) and w["category"], (
                f"'category' must be a non-empty string; got {w['category']!r}"
            )
            assert isinstance(w["message"], str) and w["message"], (
                f"'message' must be a non-empty string; got {w['message']!r}"
            )

        # Also assert backward-compat: legacy keys must still be present (original warning contract).
        for w in warnings:
            for key in ("severity", "code", "message", "source_ref"):
                assert key in w, (
                    f"backward-compat: warning must still carry '{key}'; "
                    f"got keys {set(w.keys())}"
                )

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_missing_dep_warning_surfaced(self, client: TestClient) -> None:
        """Verifies: requesting a compile with an exporter whose capability is
        'unavailable' in this env produces a structured missing_optional_dep
        warning naming the exporter.

        Requirement: missing optional deps reported.

        Strategy: find an unavailable exporter from the catalog; if none exist
        (all exporters happen to be available), use a mock to simulate one.
        """
        from logic_loom_api.adapters import learn_adapter
        from logic_loom_api.routes.learn import _WARN_CAT_MISSING_OPTIONAL_DEP

        # Find an unavailable exporter from the real catalog.
        caps = learn_adapter.get_capabilities_with_status()
        unavailable_exporters = [
            entry["capability"].removesuffix("_export")
            for entry in caps
            if entry["capability"].endswith("_export") and entry["status"] != "available"
        ]

        modes = learn_adapter.get_generation_mode_capabilities()
        available_modes = [m["capability"] for m in modes if m["status"] == "available"]
        if not available_modes:
            pytest.skip("No generation modes available")

        if unavailable_exporters:
            # Real integration path: request an actually-unavailable exporter.
            exporter_name = unavailable_exporters[0]
            body = _compile_body(available_modes[0], exporters=[exporter_name])
            r = client.post("/api/learn/compile", json=body)
            assert r.status_code == 200, (
                f"Compile with unavailable exporter must still succeed (degrade-gracefully); "
                f"got {r.status_code}: {r.text}"
            )
            data = r.json()
            warnings = data.get("warnings", [])
            dep_warnings = [
                w for w in warnings if w.get("category") == _WARN_CAT_MISSING_OPTIONAL_DEP
            ]
            assert len(dep_warnings) >= 1, (
                f"expected at least one missing_optional_dep warning for "
                f"unavailable exporter '{exporter_name}'; "
                f"warnings present: {warnings}"
            )
            dw = dep_warnings[0]
            assert "message" in dw and dw["message"], (
                f"missing_optional_dep warning must carry non-empty 'message': {dw}"
            )
            # The warning message should name the exporter.
            assert exporter_name in dw["message"], (
                f"missing_optional_dep message should name the exporter '{exporter_name}'; "
                f"got: {dw['message']}"
            )
        else:
            # All exporters are available — use a unit-level assertion on the
            # serializer with a mock (real integration path not producible in this env).
            from logic_loom_api.routes import learn as learn_route_module
            from logic_loom_api.routes.learn import _serialize_warnings

            fake_caps = [
                {"capability": "phantom_export", "status": "unavailable"},
                {"capability": "markdown_export", "status": "available"},
            ]
            result = _make_synthetic_result()
            with patch.object(
                learn_route_module.learn_adapter,
                "get_capabilities_with_status",
                return_value=fake_caps,
            ):
                out = _serialize_warnings(result, requested_exporters=["phantom"])

            dep_warnings_unit = [
                w for w in out if w.get("category") == _WARN_CAT_MISSING_OPTIONAL_DEP
            ]
            assert len(dep_warnings_unit) >= 1, (
                f"expected missing_optional_dep warning; got: {out}"
            )
            dw_unit = dep_warnings_unit[0]
            assert "message" in dw_unit and "phantom" in dw_unit["message"], (
                f"missing_optional_dep message must name the exporter; got: {dw_unit['message']}"
            )
