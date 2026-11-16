"""Tests for config: rename learn_llm_provider → llm_provider (+ alias)
and add per-consumer overrides (zsum_llm_provider, nodegen_llm_provider).

Covers:
  1. config.toml with only learn_llm_provider='codex-cli' → effective_llm_provider('learn') == 'codex-cli'.
  2. zsum_llm_provider=None ⇒ effective_llm_provider('zsum') == effective_llm_provider(global).
  3. config_get returns all four provider fields; ConfigUpdate accepts + persists them.
  4. Equality-by-construction preserved: effective_llm_provider mirrors resolve_choice's
     step-1 precedence (per-consumer override → global llm_provider → legacy alias).

Settings.effective_llm_provider is a pure precedence helper (no PATH/env probing), so
these precedence tests construct Settings directly — mirroring
test_host_llm_resolve_choice.py's style.
The ConfigUpdate round-trip tests use a FastAPI TestClient pointed at a tmp config path
(LOGIC_LOOM_CONFIG) — mirroring tests/test_app.py::TestConfig.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.config import Settings
from logic_loom_api.deps import reload_settings
from logic_loom_api.host_llm import routing
from logic_loom_api.main import api


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """TestClient with settings pointed at a tmp config.toml for file writes."""
    cfg_path = tmp_path / "config.toml"
    with patch.dict(os.environ, {"LOGIC_LOOM_CONFIG": str(cfg_path), "LOGIC_LOOM_VAULT_PATH": str(tmp_path)}):
        reload_settings()
        yield TestClient(api)
        reload_settings()


class TestLlmProviderConfig:
    """Config rename learn_llm_provider → llm_provider (+ alias) and per-consumer overrides."""

    # ── Alias-precedence matrix ──────────────────────────────────────────────

    def test_alias_precedence_both_set(self) -> None:
        """Verifies: When both llm_provider and learn_llm_provider are set, the
        global (alias) precedence rule `effective = llm_provider or learn_llm_provider`
        makes the global llm_provider win for the *global* view.

        For consumer='learn' the per-consumer field IS learn_llm_provider, so it
        wins there — that is byte-for-byte with the legacy Learn-only behaviour (the
        explicit Learn choice).
        """
        s = Settings(llm_provider="claude-cli", learn_llm_provider="codex-cli")
        # Global view: name with no <name>_llm_provider field → llm_provider wins.
        assert s.effective_llm_provider("global") == "claude-cli"
        # consumer='learn': learn_llm_provider IS the per-consumer override field,
        # so the explicit Learn choice wins (byte-for-byte with the legacy behaviour).
        assert s.effective_llm_provider("learn") == "codex-cli"
        # zsum/nodegen have no override here, so they take the global.
        assert s.effective_llm_provider("zsum") == "claude-cli"
        assert s.effective_llm_provider("nodegen") == "claude-cli"

    def test_alias_precedence_only_learn_set(self) -> None:
        """Verifies: A config with only learn_llm_provider='codex-cli' resolves
        effective_llm_provider('learn') == 'codex-cli' (back-compat alias).

        Existing user config files without llm_provider keep working.
        """
        s = Settings(learn_llm_provider="codex-cli")
        assert s.effective_llm_provider("learn") == "codex-cli"
        # And the global view (no llm_provider) falls through to the alias.
        assert s.effective_llm_provider("global") == "codex-cli"

    def test_alias_precedence_only_global_set(self) -> None:
        """Verifies: A config with only llm_provider='claude-cli' (no
        learn_llm_provider) resolves effective_llm_provider('learn') == 'claude-cli'.

        Requirement: the new global field is preferred over the (absent) legacy alias.
        """
        s = Settings(llm_provider="claude-cli")
        assert s.effective_llm_provider("learn") == "claude-cli"
        assert s.effective_llm_provider("global") == "claude-cli"

    # ── Per-consumer override set ────────────────────────────────────────────

    def test_per_consumer_fallback_zsum_override_set(self) -> None:
        """Verifies: When zsum_llm_provider is set, effective_llm_provider('zsum')
        returns it (override wins over the global).
        """
        s = Settings(llm_provider="claude-cli", zsum_llm_provider="codex-cli")
        assert s.effective_llm_provider("zsum") == "codex-cli"
        # The override is consumer-scoped: learn/nodegen still see the global.
        assert s.effective_llm_provider("learn") == "claude-cli"
        assert s.effective_llm_provider("nodegen") == "claude-cli"

    def test_per_consumer_fallback_nodegen_override_set(self) -> None:
        """Verifies: When nodegen_llm_provider is set, effective_llm_provider('nodegen')
        returns it (override wins over the global).
        """
        s = Settings(llm_provider="claude-cli", nodegen_llm_provider="codex-cli")
        assert s.effective_llm_provider("nodegen") == "codex-cli"
        assert s.effective_llm_provider("learn") == "claude-cli"
        assert s.effective_llm_provider("zsum") == "claude-cli"

    # ── Per-consumer override unset → falls back to global ───────────────────

    def test_per_consumer_fallback_zsum_unset(self) -> None:
        """Verifies: zsum_llm_provider=None ⇒ effective_llm_provider('zsum') ==
        effective_llm_provider(global).
        """
        s = Settings(llm_provider="claude-cli")  # zsum_llm_provider defaults to None
        assert s.zsum_llm_provider is None
        assert s.effective_llm_provider("zsum") == s.effective_llm_provider("global")
        assert s.effective_llm_provider("zsum") == "claude-cli"

    def test_per_consumer_fallback_nodegen_unset(self) -> None:
        """Verifies: nodegen_llm_provider=None ⇒ effective_llm_provider('nodegen') ==
        effective_llm_provider(global).
        """
        s = Settings(llm_provider="claude-cli")  # nodegen_llm_provider defaults to None
        assert s.nodegen_llm_provider is None
        assert s.effective_llm_provider("nodegen") == s.effective_llm_provider("global")
        assert s.effective_llm_provider("nodegen") == "claude-cli"

    def test_per_consumer_unset_cascades_through_alias(self) -> None:
        """Verifies: with only the legacy learn_llm_provider set, an unset
        per-consumer override cascades override → llm_provider → learn_llm_provider.

        This exercises the equality-by-construction: effective_llm_provider's
        full chain matches resolve_choice's step-1 chain for zsum/nodegen.
        """
        s = Settings(learn_llm_provider="codex-cli")  # no llm_provider, no overrides
        assert s.effective_llm_provider("zsum") == "codex-cli"
        assert s.effective_llm_provider("nodegen") == "codex-cli"
        # And resolve_choice's step-1 'effective' computes the same input. Using
        # the real resolver via SimpleNamespace would also auto-resolve, so we
        # assert the precedence input equality directly against the helper.
        for consumer in ("zsum", "nodegen"):
            effective = (
                getattr(s, f"{consumer}_llm_provider", None)
                or getattr(s, "llm_provider", None)
                or getattr(s, "learn_llm_provider", None)
            )
            assert effective == s.effective_llm_provider(consumer)

    # ── ConfigUpdate round-trip (config_get + ConfigUpdate persist/accept) ───

    def test_config_get_returns_all_four_provider_fields(self, client: TestClient) -> None:
        """Verifies: GET /api/app/config includes llm_provider, learn_llm_provider,
        zsum_llm_provider, nodegen_llm_provider.
        """
        r = client.get("/api/app/config")
        assert r.status_code == 200
        body = r.json()
        for key in (
            "llm_provider",
            "learn_llm_provider",
            "zsum_llm_provider",
            "nodegen_llm_provider",
        ):
            assert key in body, f"config_get missing {key}"

    def test_config_update_persists_all_four_fields(self, client: TestClient) -> None:
        """Verifies: POST /api/app/config with the four provider fields persists
        them to config.toml and a fresh GET reflects them.
        """
        cfg_path = Path(os.environ["LOGIC_LOOM_CONFIG"])
        payload = {
            "llm_provider": "claude-cli",
            "learn_llm_provider": "codex-cli",
            "zsum_llm_provider": "akms",
            "nodegen_llm_provider": "nlm",
        }
        r = client.post("/api/app/config", json=payload)
        assert r.status_code == 200

        import tomllib

        with cfg_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["llm_provider"] == "claude-cli"
        assert data["learn_llm_provider"] == "codex-cli"
        assert data["zsum_llm_provider"] == "akms"
        assert data["nodegen_llm_provider"] == "nlm"

        # And the reloaded config surfaces the persisted values on GET.
        body = client.get("/api/app/config").json()
        assert body["llm_provider"] == "claude-cli"
        assert body["learn_llm_provider"] == "codex-cli"
        assert body["zsum_llm_provider"] == "akms"
        assert body["nodegen_llm_provider"] == "nlm"

    def test_config_update_accepts_all_four_fields(self, client: TestClient) -> None:
        """Verifies: the ConfigUpdate whitelist accepts the three new provider
        fields (+ the kept alias) — POST succeeds 200 and echoes them as applied.
        """
        payload = {
            "llm_provider": "claude-cli",
            "zsum_llm_provider": "codex-cli",
            "nodegen_llm_provider": "akms",
        }
        r = client.post("/api/app/config", json=payload)
        assert r.status_code == 200
        applied = r.json()["applied"]
        assert applied["llm_provider"] == "claude-cli"
        assert applied["zsum_llm_provider"] == "codex-cli"
        assert applied["nodegen_llm_provider"] == "akms"
