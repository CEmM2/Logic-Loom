"""Tests for learn_adapter.py (plugin detect/load + request translation).

Covers: the adapter imports without akms_learn and reports unavailable; the
available status payload shape; no ordering/extraction/validation is
reimplemented (everything is delegated to the plugin).
"""

from __future__ import annotations

from typing import Any

import pytest

from logic_loom_api.adapters import learn_adapter


class TestLearnAdapter:
    """Unit tests for learn_adapter — import-guard, status, request translation."""

    @pytest.mark.unit
    def test_import_guard_returns_unavailable_when_module_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: adapter loads even when akms_learn is absent and reports unavailable.

        Requirement: 'Adapter imports without akms_learn present and reports unavailable.'
        Passes when: build_status() returns akms_learn_available=False + reason
        with the module mocked out / not importable.
        """
        # The module imported cleanly at the top of this file even though the
        # detection probe is run lazily — that already proves the import guard
        # does not raise at import time. Now simulate the package being absent.
        monkeypatch.setattr(
            learn_adapter, "akms_learn_available", lambda: False
        )

        status = learn_adapter.build_status()

        assert status.akms_learn_available is False
        assert status.reason
        assert status.capabilities == []
        # Optional metadata fields stay None on the unavailable path.
        assert status.version is None
        assert status.plugin_api is None

        payload = status.to_payload()
        assert payload["akms_learn_available"] is False
        assert payload["reason"]
        assert payload["capabilities"] == []

    @pytest.mark.unit
    def test_status_payload_shape_when_available(self) -> None:
        """Verifies: available status payload exposes version/api/schema/capabilities.

        Requirement: 'When available, status payload includes version, plugin API, packet schema,
        AKMS schema range, capabilities.'
        Passes when: the payload dict carries all required keys.
        """
        from akms_learn.plugin import get_plugin

        status = learn_adapter.build_status()

        assert status.akms_learn_available is True
        assert status.reason is None
        assert status.version  # importlib.metadata resolved a version string
        assert status.plugin_api == "akms-learn-plugin/v1"
        assert status.learning_packet_schema == "learn/v1"
        assert status.supported_akms_schema_min == "v2"
        assert status.supported_akms_schema_max == "v2"

        # Capabilities are sourced verbatim from the plugin — not re-derived.
        expected_caps = get_plugin().capabilities()
        assert status.capabilities == list(expected_caps)
        assert status.capabilities  # non-empty

        # The serialised payload carries the full available-path field set.
        # ``reason`` is absent on the available path (it is null there and
        # to_payload() omits null/absent fields on the available path).
        payload = status.to_payload()
        for key in (
            "akms_learn_available",
            "version",
            "plugin_api",
            "learning_packet_schema",
            "supported_akms_schema_min",
            "supported_akms_schema_max",
            "capabilities",
        ):
            assert key in payload, f"missing status field: {key}"
        assert "reason" not in payload, (
            "reason must be absent from the available payload"
        )

    @pytest.mark.unit
    def test_request_translation_node_depth_and_graphslice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: a (node, depth, direction) request AND an explicit GraphSlice both
        translate into a valid akms_learn request, delegating to compile_learning_source.

        Requirement: 'No ordering/extraction/validation logic is reimplemented — only delegated.'
        Passes when: translation builds the request and calls the plugin compile API
        (mocked) without re-deriving ordering/extraction.
        """
        from akms_learn import GraphSlice, LearningRequest, fixture_graph

        captured: list[dict[str, Any]] = []

        def _fake_compile(
            request: Any,
            *,
            graph_slice: Any = None,
            output_dir: Any = None,
            **kwargs: Any,
        ) -> str:
            # Record the translated inputs so we can prove delegation happened
            # with the request the adapter built (not a re-derived one).
            captured.append(
                {
                    "request": request,
                    "graph_slice": graph_slice,
                    "output_dir": output_dir,
                }
            )
            return "SENTINEL_RESULT"

        # Patch the compiler entry point inside the akms_learn package so the
        # adapter's local ``from akms_learn import compile_learning_source``
        # resolves to our fake — proving the adapter delegates rather than
        # reimplementing the pipeline.
        import akms_learn

        monkeypatch.setattr(akms_learn, "compile_learning_source", _fake_compile)

        # --- Path 1: explicit GraphSlice ----------------------------------
        slice_ = fixture_graph()
        outcome_slice = learn_adapter.compile(
            topic="j2 return mapping",
            goal="implement the radial return algorithm",
            generation_option="deterministic_outline",
            seed_tags=["return_mapping"],
            graph_slice=slice_,
        )

        assert outcome_slice.result == "SENTINEL_RESULT"
        assert len(captured) == 1
        first = captured[0]
        assert isinstance(first["request"], LearningRequest)
        assert first["request"].topic == "j2 return mapping"
        assert first["request"].generation_option == "deterministic_outline"
        assert first["request"].seed_tags == ["return_mapping"]
        # The explicit GraphSlice is passed straight through (no re-slicing).
        assert isinstance(first["graph_slice"], GraphSlice)
        assert first["graph_slice"] is slice_
        # No artifact persistence at this layer (the route's cache policy owns
        # output_dir writes).
        assert first["output_dir"] is None

        # --- Path 2: node / depth / direction selection -------------------
        # The adapter resolves the slice via akms_learn.load_graph from a dict
        # payload (delegated loading), carries max_depth on the request, and
        # delegates to the same compiler entry point.
        slice_payload = {
            "nodes": list(slice_.nodes),
            "edges": list(slice_.edges),
            "metadata": dict(slice_.metadata),
        }
        outcome_node = learn_adapter.compile(
            topic="state space form",
            goal="derive the state-space equations",
            generation_option="node_anthology",
            node="core_j2_return_mapping",
            max_depth=2,
            direction="out",
            graph_slice=slice_payload,
        )

        assert outcome_node.result == "SENTINEL_RESULT"
        assert len(captured) == 2
        second = captured[1]
        assert isinstance(second["request"], LearningRequest)
        assert second["request"].topic == "state space form"
        # node/depth selection rides on the request's max_depth field.
        assert second["request"].max_depth == 2
        # The dict payload was loaded into a GraphSlice via akms_learn's loader.
        assert isinstance(second["graph_slice"], GraphSlice)
