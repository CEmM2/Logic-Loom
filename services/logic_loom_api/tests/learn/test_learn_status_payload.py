"""Tests for the /api/learn/status payload (available/unavailable).

Covers: the unavailable payload matches the contract exactly (3 keys, nothing
else); the available payload includes all required fields with correct types
and values.

Contract is enforced in LearnStatus.to_payload() (learn_adapter.py).
Tests exercise the serializer directly (unit tier) using monkeypatching to
simulate plugin presence/absence — no HTTP overhead required.
"""

from __future__ import annotations

import pytest

from logic_loom_api.adapters import learn_adapter


class TestLearnStatusPayload:
    """Unit tests for the /api/learn/status response contract."""

    @pytest.mark.unit
    def test_unavailable_payload_exact_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: unavailable status == {akms_learn_available: false, reason, capabilities: []}.

        Requirement: 'Unavailable payload matches the contract exactly (false + reason + empty capabilities).'
        Passes when: key set is EXACTLY {"akms_learn_available", "reason", "capabilities"},
        akms_learn_available is False, reason is a non-empty string, capabilities == [].

        Contract example (verbatim):
          {"akms_learn_available": false, "reason": "...", "capabilities": []}
        Null metadata fields (version, plugin_api, …) MUST NOT appear.
        """
        # Simulate the package being absent.
        monkeypatch.setattr(learn_adapter, "akms_learn_available", lambda: False)

        status = learn_adapter.build_status()
        payload = status.to_payload()

        # Key set must be EXACTLY 3 — no null metadata leakage.
        assert set(payload.keys()) == {"akms_learn_available", "reason", "capabilities"}, (
            f"Unavailable payload must have exactly 3 keys, got: {set(payload.keys())}"
        )

        # Value assertions.
        assert payload["akms_learn_available"] is False, (
            "akms_learn_available must be False on the unavailable path"
        )
        assert isinstance(payload["reason"], str) and payload["reason"], (
            "reason must be a non-empty string on the unavailable path"
        )
        assert payload["capabilities"] == [], (
            "capabilities must be [] on the unavailable path"
        )

    @pytest.mark.unit
    def test_available_payload_required_keys_present(self) -> None:
        """Verifies: available status includes version, plugin API, packet schema,
        AKMS schema range, capabilities.

        Requirement: 'Available payload includes version, plugin API, packet schema, schema range,
        capabilities.'
        Passes when: all required available-path keys are present with correctly
        typed values; akms_learn_available is True; capabilities is a non-empty list
        equal to get_plugin().capabilities(); version is a non-empty string;
        plugin_api == "akms-learn-plugin/v1", learning_packet_schema == "learn/v1",
        supported_akms_schema_min == "v2", supported_akms_schema_max == "v2".
        """
        from akms_learn.plugin import get_plugin

        status = learn_adapter.build_status()
        payload = status.to_payload()

        # akms_learn must be available in the dev/test environment.
        assert payload["akms_learn_available"] is True, (
            "akms_learn must be available in the test environment for this test; "
            "ensure akms_learn is installed (uv sync --extra akms)"
        )

        # Required keys must all be present.
        required_keys = {
            "akms_learn_available",
            "version",
            "plugin_api",
            "learning_packet_schema",
            "supported_akms_schema_min",
            "supported_akms_schema_max",
            "capabilities",
        }
        missing = required_keys - set(payload.keys())
        assert not missing, f"Available payload missing required keys: {missing}"

        # reason must be absent on the available path.
        assert "reason" not in payload, (
            "reason must be absent from the available payload"
        )

        # Value / type correctness.
        assert isinstance(payload["version"], str) and payload["version"], (
            "version must be a non-empty string"
        )
        assert payload["plugin_api"] == "akms-learn-plugin/v1", (
            f"plugin_api mismatch: {payload['plugin_api']!r}"
        )
        assert payload["learning_packet_schema"] == "learn/v1", (
            f"learning_packet_schema mismatch: {payload['learning_packet_schema']!r}"
        )
        assert payload["supported_akms_schema_min"] == "v2", (
            f"supported_akms_schema_min mismatch: {payload['supported_akms_schema_min']!r}"
        )
        assert payload["supported_akms_schema_max"] == "v2", (
            f"supported_akms_schema_max mismatch: {payload['supported_akms_schema_max']!r}"
        )

        # Capabilities sourced verbatim from the plugin.
        expected_caps = list(get_plugin().capabilities())
        assert isinstance(payload["capabilities"], list), "capabilities must be a list"
        assert payload["capabilities"], "capabilities must be non-empty"
        assert payload["capabilities"] == expected_caps, (
            f"capabilities mismatch: {payload['capabilities']!r} != {expected_caps!r}"
        )
