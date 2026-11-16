"""Tests for unified discovery — GET /api/llm/providers with per-consumer effective choice.

Covers:
  1 (Response shape: providers[] + effective{learn,zsum,nodegen}),
  2 (Alias: /api/learn/providers kept in its legacy shape {providers, selected}; /api/llm/providers
     is the superset adding `effective`; they share the providers availability list),
  3 (Availability reflects shutil.which + key presence, monkeypatched).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.deps import reload_settings
from logic_loom_api.main import api


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """TestClient with an isolated config so tests don't touch the real config."""
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        },
    ):
        reload_settings()
        yield TestClient(api)
        reload_settings()


class TestLlmProvidersDiscovery:
    """Tests for the unified discovery endpoint with per-consumer effective choice."""

    def test_llm_providers_payload_shape(self, client: TestClient) -> None:
        """Verifies: GET /api/llm/providers response includes providers[] array and effective object with learn/zsum/nodegen keys.

        Requirement: 'Response includes providers: [...] (availability) and effective: {learn, zsum, nodegen}.'
        Passes when: response contains 'providers' (list of provider objects with name, available, route),
        and 'effective' (dict with 'learn', 'zsum', 'nodegen' keys, each mapped to a provider name or None).
        Also asserts: endpoint returns 200 with no provider configured (read-only / never 503 — degrade gracefully).
        """
        r = client.get("/api/llm/providers")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()

        # providers must be a list of objects
        assert isinstance(body["providers"], list), "providers must be a list"
        assert len(body["providers"]) > 0, "providers list must not be empty"
        for row in body["providers"]:
            assert "name" in row, f"provider row missing 'name': {row}"
            assert "available" in row, f"provider row missing 'available': {row}"

        # effective must be a dict with exactly {learn, zsum, nodegen}
        assert "effective" in body, "response must contain 'effective'"
        effective = body["effective"]
        assert isinstance(effective, dict), "effective must be a dict"
        assert set(effective.keys()) == {"learn", "zsum", "nodegen"}, (
            f"effective keys must be exactly {{learn,zsum,nodegen}}, got: {set(effective.keys())}"
        )

        # Legacy back-compat: selected must also be present
        assert "selected" in body, "response must contain 'selected' (legacy back-compat)"

        # With no provider configured, all effective values are None — endpoint is read-only
        # and must never 503 even when nothing is set (degrade gracefully).
        assert effective["learn"] is None or isinstance(effective["learn"], str)
        assert effective["zsum"] is None or isinstance(effective["zsum"], str)
        assert effective["nodegen"] is None or isinstance(effective["nodegen"], str)

    def test_llm_providers_alias_parity_with_learn_providers(self, client: TestClient) -> None:
        """Verifies: /api/learn/providers is kept as a read-only alias of the same
        discovery source, while /api/llm/providers is its superset with `effective`.

        Requirement: '/api/learn/providers is kept as a read-only never-503 alias in its legacy
        shape — exactly {providers, selected}; the effective key belongs to
        /api/llm/providers ONLY.' The Learn repoint freezes /api/learn/providers to exactly
        {providers, selected} with raw selected, so the two endpoints are NOT
        byte-identical — they share the `providers` availability data.
        Passes when: both 200; learn keys == {providers, selected}; llm keys ==
        {providers, selected, effective}; both `providers` lists are equal.
        """
        r_llm = client.get("/api/llm/providers")
        r_learn = client.get("/api/learn/providers")

        assert r_llm.status_code == 200, f"/api/llm/providers: {r_llm.status_code}"
        assert r_learn.status_code == 200, f"/api/learn/providers: {r_learn.status_code}"

        body_llm = r_llm.json()
        body_learn = r_learn.json()

        # /api/learn/providers stays in its legacy shape (byte-identical freeze):
        # exactly {providers, selected}, NO effective key.
        assert set(body_learn.keys()) == {"providers", "selected"}, (
            f"/api/learn/providers must keep legacy shape {{providers, selected}}, "
            f"got: {set(body_learn.keys())}"
        )

        # /api/llm/providers is the unified generalisation: adds the per-consumer
        # effective map on top of the shared discovery payload.
        assert set(body_llm.keys()) == {"providers", "selected", "effective"}, (
            f"/api/llm/providers must be {{providers, selected, effective}}, "
            f"got: {set(body_llm.keys())}"
        )

        # Shared discovery source: the availability list is identical.
        assert body_llm["providers"] == body_learn["providers"], (
            "both endpoints must report the same provider availability list"
        )

    def test_llm_providers_availability_monkeypatched_which(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: availability field reflects shutil.which results.

        Monkeypatches `logic_loom_api.host_llm.discovery.shutil.which` so that
        only 'claude' is on PATH, asserts that 'claude-cli' row becomes available
        while a CLI that is NOT on path (e.g. 'codex') remains unavailable.
        Mirrors the pattern from test_learn_llm_providers.py::TestDiscovery::test_available_builtins_on_path.
        """
        import logic_loom_api.host_llm.discovery as disc

        def _which_only_claude(binary: str) -> str | None:
            return f"/usr/bin/{binary}" if binary == "claude" else None

        monkeypatch.setattr(disc.shutil, "which", _which_only_claude)

        # Also clear API keys so only CLI availability is deterministic
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        r = client.get("/api/llm/providers")
        assert r.status_code == 200
        body = r.json()

        rows_by_name = {row["name"]: row for row in body["providers"]}

        # claude is on PATH → claude-cli must be available
        assert "claude-cli" in rows_by_name, "claude-cli must appear in providers"
        assert rows_by_name["claude-cli"]["available"] is True, (
            "claude-cli must be available when 'claude' is on PATH"
        )

        # codex is NOT on PATH → codex-cli must be unavailable
        assert "codex-cli" in rows_by_name, "codex-cli must appear in providers"
        assert rows_by_name["codex-cli"]["available"] is False, (
            "codex-cli must not be available when 'codex' is not on PATH"
        )

    def test_discovery_degrades_on_malformed_custom_cli(self, client: TestClient) -> None:
        """Regression: a malformed custom CLI
        (unbalanced quote) persisted via ConfigUpdate must NOT 500 the discovery
        endpoints — discover_providers degrades the bad row to unavailable.

        Exercises the exact attack path: the config API accepts + persists any
        custom-CLI string, and both GET surfaces call discover_providers without a
        guard. Both /api/llm/providers and the /api/learn/providers alias must 200.
        """
        save = client.post(
            "/api/app/config", json={"learn_llm_custom_clis": {"bad": '"unterminated'}}
        )
        assert save.status_code == 200, f"config save failed: {save.text}"

        for path in ("/api/llm/providers", "/api/learn/providers"):
            r = client.get(path)
            assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text}"

        body = client.get("/api/llm/providers").json()
        bad = next((row for row in body["providers"] if row["name"] == "bad"), None)
        assert bad is not None, "malformed custom CLI row must still be reported"
        assert bad["available"] is False
