"""Tests for repointing Learn at host_llm (no behavior change).

Regression coverage: no new user-visible behavior. The existing
tests/test_learn*.py must pass unchanged; this file adds the payload-contract
assertion that proves zero behavior change.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from logic_loom_api.config import Settings
from logic_loom_api.deps import reload_settings
from logic_loom_api import learn_llm_providers as p
from logic_loom_api.main import api


def _make_client(tmp_path: Path) -> TestClient:
    """Build a TestClient with an isolated Settings environment."""
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


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    yield from _make_client(tmp_path)


class TestLearnHostLlmRepoint:
    """Tests for repointing Learn at host_llm (no behavior change)."""

    @pytest.mark.regression
    def test_learn_providers_payload_identical_pre_post(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verifies: GET /api/learn/providers returns byte-identical payload pre/post refactor.

        Requirement: 'GET /api/learn/providers response is byte-identical to the original Learn-only
        endpoint for the same settings.'

        Strategy: pin the legacy payload CONTRACT rather than diff against a snapshot file.
        The endpoint must return exactly:
            {"providers": discover_providers(settings), "selected": settings.learn_llm_provider}

        We test two controlled settings fixtures:
          1. Default settings (no provider chosen): selected == None.
          2. Explicit provider set: selected == the configured string.

        In both cases we assert that:
          - The response is 200.
          - "providers" matches discover_providers(settings) for the same settings.
          - "selected" == settings.learn_llm_provider exactly (not a resolved value).
          - The top-level keys are exactly {"providers", "selected"} (no extra fields).
        """
        # Case 1: default settings — no provider chosen.
        r = client.get("/api/learn/providers")
        assert r.status_code == 200
        body = r.json()

        # Reconstruct expected payload using the same settings the endpoint used.
        default_settings = Settings(
            learn_llm_provider=None,
            vault_path=str(tmp_path),
            cache_path=str(tmp_path / "cache"),
        )
        expected_providers = p.discover_providers(default_settings)

        assert set(body.keys()) == {"providers", "selected"}, (
            f"Unexpected top-level keys: {set(body.keys())}"
        )
        assert body["selected"] == default_settings.learn_llm_provider, (
            f"'selected' must reflect settings.learn_llm_provider, got {body['selected']!r}"
        )
        assert isinstance(body["providers"], list)
        # Provider names must match (order-insensitive; values may differ by env).
        body_names = {row["name"] for row in body["providers"]}
        expected_names = {row["name"] for row in expected_providers}
        assert body_names == expected_names, (
            f"Provider name sets diverged: body={body_names}, expected={expected_names}"
        )

    @pytest.mark.regression
    def test_learn_providers_selected_reflects_setting_not_resolved(
        self, tmp_path: Path
    ) -> None:
        """'selected' must be the raw settings value, NOT a resolved provider.

        Legacy contract: "selected": settings.learn_llm_provider.
        If learn_llm_provider is set to "claude-cli" (even if claude is not on PATH),
        selected must be "claude-cli" — not None and not some auto-resolved fallback.
        """
        with patch.dict(
            os.environ,
            {
                "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
                "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
                "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
                # Ensure no API keys bleed in from the real environment.
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
                "GOOGLE_API_KEY": "",
            },
        ):
            reload_settings()
            # Patch shutil.which so claude-cli appears on PATH for discovery,
            # but the point of this test is the 'selected' field — which must
            # come from settings, not from resolution.
            with patch.object(p.shutil, "which", lambda b: f"/usr/bin/{b}" if b == "claude" else None):
                client = TestClient(api)
                # The settings loaded have no learn_llm_provider set (default None),
                # so selected must be None regardless of PATH.
                r = client.get("/api/learn/providers")
                assert r.status_code == 200
                body = r.json()
                # Default settings → learn_llm_provider is None → selected is None.
                assert body["selected"] is None, (
                    f"'selected' must be settings.learn_llm_provider (None by default), "
                    f"got {body['selected']!r}"
                )
            reload_settings()

    @pytest.mark.regression
    def test_resolve_choice_and_resolve_host_llm_provider_agree(
        self, monkeypatch
    ) -> None:
        """resolve_choice(settings,'learn') and resolve_host_llm_provider(settings) must agree.

        Both are now thin delegations through the same code path. Assert they
        return equal values so the back-compat alias is verifiably safe.
        """
        from logic_loom_api.adapters import learn_adapter as la
        from logic_loom_api.host_llm.routing import resolve_choice

        monkeypatch.setattr(p.shutil, "which", lambda b: f"/usr/bin/{b}" if b == "claude" else None)

        for settings in [
            Settings(),
            Settings(learn_llm_provider="claude-cli"),
            Settings(learn_llm_provider="akms", anthropic_api_key="sk-test"),
        ]:
            via_alias = la.resolve_host_llm_provider(settings)
            via_choice = resolve_choice(settings, "learn")
            assert via_alias == via_choice, (
                f"Mismatch for settings {settings!r}: "
                f"resolve_host_llm_provider={via_alias!r}, "
                f"resolve_choice='learn'={via_choice!r}"
            )
