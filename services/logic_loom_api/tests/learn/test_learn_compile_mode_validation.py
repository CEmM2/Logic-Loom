"""Tests for schema validation of all compile request modes.

Covers: each mode validates its required fields; invalid requests are rejected
cleanly.

The per-mode gate Logic-Loom owns is a thin, mode-aware request-shape
check that runs in ``learn_compile()`` BEFORE delegating to ``akms_learn``:

  1. core fields (topic, goal) present and non-empty for every mode,
  2. ``generation_option`` is a KNOWN generation mode (driven by the akms_learn
     catalog, never hard-coded), else 422 ``unknown_mode``,
  3. the mode is AVAILABLE (optional extras installed), else 412
     ``capability_unavailable``.

akms_learn owns capability availability rules, schema checks, per-mode field
SEMANTICS, and packet validation — this gate does not reimplement those; it
surfaces a clean 4xx instead of a 500 or a silently-wrong artifact.

Cases:
  - test_valid_per_mode_requests_accepted  — a well-formed request for every
    AVAILABLE catalog mode passes the validation gate (not rejected with a
    validation 4xx). It may still fail later for graph reasons (412
    graph_unavailable) — that is fine; the point is the gate accepts the shape.
  - test_invalid_mode_requests_rejected     — a request with a missing
    mode-required field, and a request with an unknown generation_option, are
    each rejected with a structured 4xx (asserting status + envelope
    error.code/message), never a 500.

Patterns reused from test_learn_profiles_exporters.py / test_learn_backend_suite.py:
  - HAS_AKMS_LEARN / skipif idiom
  - tmp-path TestClient fixture (patch.dict env + reload_settings + TestClient)
  - mode list driven from learn_adapter.get_generation_mode_capabilities()
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.adapters import learn_adapter

# ---------------------------------------------------------------------------
# Module-level availability check
# ---------------------------------------------------------------------------

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None


# ---------------------------------------------------------------------------
# Shared fixture (mirrors test_learn_profiles_exporters.py::client /
# test_learn_backend_suite.py::client)
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
# Helpers
# ---------------------------------------------------------------------------


def _fixture_graph_slice_payload() -> dict:
    """A minimal in-memory GraphSlice payload that compiles successfully.

    Two nodes with markdown bodies + a single edge so the deterministic
    compiler has something to order. Mirrors the backend-suite fixture shape but kept
    self-contained so this test does not import that suite's internals.
    """
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


# Status codes a request can return AFTER the per-mode gate accepts its shape.
# A shape-valid request may still legitimately fail downstream — e.g. a 412
# graph_unavailable from the node/depth path, or a 200/500 compile outcome.
# What it must NOT be is a 4xx emitted BY the validation gate (422 unknown_mode /
# 422 compile_precondition_error / 412 capability_unavailable).
_GATE_REJECTION_CODES = {
    "unknown_mode",
    "compile_precondition_error",
    "capability_unavailable",
}


def _error_code(response) -> str | None:
    """Return envelope error.code if the body is a structured error, else None."""
    try:
        return response.json().get("error", {}).get("code")
    except Exception:  # pragma: no cover - non-JSON body
        return None


# ---------------------------------------------------------------------------
# Per-mode compile request validation
# ---------------------------------------------------------------------------


class TestCompileModeValidation:
    """Tests for per-mode compile request validation."""

    # -----------------------------------------------------------------------
    # Case (a): valid per-mode requests pass the validation gate
    # -----------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_valid_per_mode_requests_accepted(self, client: TestClient) -> None:
        """Verifies: a well-formed compile request for EVERY available generation
        mode passes the per-mode validation gate (no gate-emitted 4xx).

        Drives the mode list from the akms_learn catalog (no hard-coding) and
        covers every AVAILABLE mode — so the test stays in sync with the catalog
        and exercises the whole "valid per-mode requests accepted" surface.

        A shape-valid request may still fail downstream for graph/compile
        reasons; what it must never be is a gate rejection (unknown_mode /
        compile_precondition_error / capability_unavailable) and never a 500.
        """
        modes = learn_adapter.get_generation_mode_capabilities()
        assert modes, "Plugin must expose at least one generation mode"

        available = [m["capability"] for m in modes if m["status"] == "available"]
        assert available, "At least one generation mode must be available to test the accept path"

        for mode in available:
            body = _compile_body(mode)
            r = client.post("/api/learn/compile", json=body)

            # Never a server error from a well-formed request.
            assert r.status_code != 500, (
                f"mode {mode!r}: well-formed request must not 500; got {r.status_code}: {r.text}"
            )

            # Must not be rejected by the per-mode validation gate.
            code = _error_code(r)
            assert code not in _GATE_REJECTION_CODES, (
                f"mode {mode!r}: well-formed request was rejected by the validation gate "
                f"(status {r.status_code}, code {code!r}); it should pass the gate. Body: {r.text}"
            )

            # Positive expectation: with a real graph_slice an available mode
            # compiles to 200. (No node/depth path here, so no 412.)
            assert r.status_code == 200, (
                f"mode {mode!r}: available mode with a graph_slice should compile to 200; "
                f"got {r.status_code}: {r.text}"
            )

    # -----------------------------------------------------------------------
    # Case (b): invalid mode requests rejected cleanly with structured 4xx
    # -----------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_invalid_mode_requests_rejected(self, client: TestClient) -> None:
        """Verifies: invalid compile requests are rejected with a structured 4xx
        (envelope error.code + message), never a 500.

        Three malformed cases:
          1. unknown generation_option            -> 422 unknown_mode
          2. missing a mode-required core field   -> 422 compile_precondition_error
          3. a known-but-unavailable mode (if the catalog has one)
                                                  -> 412 capability_unavailable

        The valid/invalid mode sets are derived from the catalog so the test
        never hard-codes capability names.
        """
        # 1) Unknown generation_option -> 422 unknown_mode, naming valid modes.
        r_unknown = client.post(
            "/api/learn/compile",
            json=_compile_body("totally_not_a_real_mode_xyz"),
        )
        assert r_unknown.status_code == 422, (
            f"unknown mode must be 422 (not {r_unknown.status_code}): {r_unknown.text}"
        )
        body_unknown = r_unknown.json()
        assert "error" in body_unknown, f"unknown-mode response must be an envelope: {body_unknown}"
        assert body_unknown["error"]["code"] == "unknown_mode", (
            f"unknown-mode code must be 'unknown_mode', got {body_unknown['error']['code']!r}"
        )
        assert "totally_not_a_real_mode_xyz" in body_unknown["error"]["message"], (
            "unknown-mode message must name the offending mode"
        )
        # Detail surfaces the valid mode set (catalog-driven).
        valid_modes = body_unknown["error"].get("detail", {}).get("valid_modes")
        assert isinstance(valid_modes, list) and valid_modes, (
            "unknown-mode detail must list the valid modes"
        )

        # 2) Missing a mode-required core field (goal) -> 422 compile_precondition_error.
        valid_mode = next(
            (
                m["capability"]
                for m in learn_adapter.get_generation_mode_capabilities()
                if m["status"] == "available"
            ),
            None,
        )
        assert valid_mode is not None, "need one available mode to test the missing-field path"

        body_missing = _compile_body(valid_mode)
        body_missing["goal"] = "   "  # present but blank → counts as missing
        r_missing = client.post("/api/learn/compile", json=body_missing)
        assert r_missing.status_code == 422, (
            f"missing required field must be 422 (not {r_missing.status_code}): {r_missing.text}"
        )
        body_missing_json = r_missing.json()
        assert body_missing_json["error"]["code"] == "compile_precondition_error", (
            f"missing-field code must be 'compile_precondition_error', "
            f"got {body_missing_json['error']['code']!r}"
        )
        assert "goal" in body_missing_json["error"]["message"], (
            "missing-field message must name the offending field ('goal')"
        )
        assert "goal" in (body_missing_json["error"].get("detail", {}).get("fields") or []), (
            "missing-field detail must list 'goal' in fields"
        )

        # 3) Known-but-unavailable mode -> 412 capability_unavailable (when catalog has one).
        unavailable_mode = next(
            (
                m["capability"]
                for m in learn_adapter.get_generation_mode_capabilities()
                if m["status"] != "available"
            ),
            None,
        )
        if unavailable_mode is not None:
            r_unavail = client.post(
                "/api/learn/compile", json=_compile_body(unavailable_mode)
            )
            assert r_unavail.status_code == 412, (
                f"unavailable mode must be 412 (not {r_unavail.status_code}): {r_unavail.text}"
            )
            assert r_unavail.status_code != 500
            body_unavail = r_unavail.json()
            assert body_unavail["error"]["code"] == "capability_unavailable", (
                f"unavailable-mode code must be 'capability_unavailable', "
                f"got {body_unavail['error']['code']!r}"
            )
            assert unavailable_mode in body_unavail["error"]["message"], (
                "unavailable-mode message must name the offending mode"
            )

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_absent_and_blank_core_field_share_one_code(self, client: TestClient) -> None:
        """An ABSENT topic/goal and a BLANK one must BOTH surface under the same
        422 ``compile_precondition_error``. Previously an absent field
        was caught earlier by pydantic as a raw ``validation_error`` — two
        different 422 codes for the same 'missing topic/goal' failure."""
        valid_mode = next(
            (m["capability"] for m in learn_adapter.get_generation_mode_capabilities()
             if m["status"] == "available"),
            None,
        )
        assert valid_mode is not None, "need one available mode"

        # Absent: 'topic' key omitted entirely.
        body_absent = _compile_body(valid_mode)
        body_absent.pop("topic", None)
        r_absent = client.post("/api/learn/compile", json=body_absent)

        # Blank: present-but-whitespace.
        r_blank = client.post("/api/learn/compile", json=_compile_body(valid_mode, topic="   "))

        for label, r in (("absent", r_absent), ("blank", r_blank)):
            assert r.status_code == 422, f"{label} topic must be 422: {r.text}"
            assert _error_code(r) == "compile_precondition_error", (
                f"{label} topic must yield compile_precondition_error, got {_error_code(r)!r}: {r.text}"
            )

    # -----------------------------------------------------------------------
    # Regression: /status.generation_modes must stay consistent
    # with the compile gate, so the UI default selection never 422s.
    # -----------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_status_generation_modes_consistent_with_gate(self, client: TestClient) -> None:
        """Every mode advertised by ``GET /status.generation_modes`` is accepted by
        the ``POST /compile`` gate (never ``unknown_mode``); infra capabilities are
        excluded from it (regression guard).

        The frontend mode selector derives its options — and its DEFAULT (first
        entry) — from ``status.generation_modes``. Before this fix the selector
        partitioned the RAW ``capabilities`` list by the ``_export`` suffix only,
        so the first capability (``learning_source_packet``, an infra string) became
        the default ``generation_option`` and the gate rejected it with
        ``422 unknown_mode``. This test pins both halves of the contract.
        """
        status = client.get("/api/learn/status").json()
        assert status["akms_learn_available"] is True, status

        modes = status.get("generation_modes")
        assert isinstance(modes, list) and modes, (
            f"available /status must advertise a non-empty generation_modes list: {status}"
        )

        # generation_modes is the filtered catalog — never the raw capability list.
        assert modes == [
            m["capability"] for m in learn_adapter.get_generation_mode_capabilities()
        ], "generation_modes must equal the filtered generation-mode catalog"

        # Infra capabilities live in capabilities but must NOT be advertised as modes.
        infra_in_caps = [c for c in status["capabilities"] if c in learn_adapter._INFRA_CAPABILITIES]
        assert infra_in_caps, "fixture plugin should expose at least one infra capability"
        assert not (set(infra_in_caps) & set(modes)), (
            f"infra capabilities leaked into generation_modes: {set(infra_in_caps) & set(modes)}"
        )

        # The UI default = generation_modes[0]: posting it must never be unknown_mode.
        r_default = client.post("/api/learn/compile", json=_compile_body(modes[0]))
        assert _error_code(r_default) != "unknown_mode", (
            f"default mode {modes[0]!r} from status.generation_modes was rejected as "
            f"unknown_mode — status/gate drift: {r_default.text}"
        )

        # And the OLD heuristic default (raw capabilities[0]) is exactly the value
        # the gate rejects — documents the regression this fix closes.
        raw_default = status["capabilities"][0]
        if raw_default not in modes:
            r_raw = client.post("/api/learn/compile", json=_compile_body(raw_default))
            assert _error_code(r_raw) == "unknown_mode", (
                f"expected raw capabilities[0] {raw_default!r} to be rejected unknown_mode "
                f"(the bug the generation_modes field fixes): {r_raw.text}"
            )

    # -----------------------------------------------------------------------
    # Degrade-gracefully: plugin absent -> structured 503, never 500
    # -----------------------------------------------------------------------

    @pytest.mark.integration
    def test_compile_plugin_absent_returns_503_not_validation_error(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the plugin is absent the compile path returns the structured 503
        (DEGRADE-GRACEFULLY) — the per-mode gate (which needs the catalog) must
        not turn this into a spurious validation 4xx or a 500."""
        monkeypatch.setattr(learn_adapter, "akms_learn_available", lambda: False)

        r = client.post("/api/learn/compile", json=_compile_body("deterministic_outline"))
        assert r.status_code == 503, (
            f"plugin-absent compile must be 503 (not {r.status_code}): {r.text}"
        )
        assert r.json()["error"]["code"] == "plugin_unavailable"
