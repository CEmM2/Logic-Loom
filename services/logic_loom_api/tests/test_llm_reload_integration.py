"""Tests for unified-reload cross-consumer integration.

Proves that a single ``POST /api/llm/providers/reload`` rewires all three
consumers (Learn + zsum + node-gen) in one call, asserted **through each
adapter's resolved runtime reflecting the reloaded settings**, NOT through the
``rewire_host_llm`` ``"zsum"``/``"nodegen"`` response slots (both ``[]`` by
design: dispatch env is applied per run, not as global state, so asserting
the empty slots would fail or pass vacuously).

Covers: one reload call updates all three consumer runtimes.

Drives the FastAPI TestClient against a tmp ``cache_path`` (CLAUDE.md: tests
hitting ``/api`` runs need a tmp cache_path to avoid clobbering user history).
The consumer resolvers are import-safe without the akms extras (they degrade to
``None``), so this file passes in a core (no-akms) env too.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import logic_loom_api.host_llm.discovery as discovery
from logic_loom_api.adapters import zsum_adapter
from logic_loom_api.akms_dispatch import resolve_node_gen_runtime
from logic_loom_api.config import Settings
from logic_loom_api.host_llm.routing import resolve_choice

# All completion key env vars that could flip resolution if exported in CI.
_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AKMS_LLM_API_BASE",
)
_NLM_ENV_VARS = (
    "AKMS_LEARN_NLM_NOTEBOOK_ID",
    "NLM_NOTEBOOK_ID",
)
_PROVIDER_CONFIG_VARS = (
    "LOGIC_LOOM_LLM_PROVIDER",
    "LOGIC_LOOM_LEARN_LLM_PROVIDER",
    "LOGIC_LOOM_ZSUM_LLM_PROVIDER",
    "LOGIC_LOOM_NODEGEN_LLM_PROVIDER",
)


@pytest.fixture(autouse=True)
def _scrub_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove completion-key, NLM, and LOGIC_LOOM provider env vars.

    Without this, a CI-exported ANTHROPIC_API_KEY would flip auto-resolution
    and false-FAIL the no-choice assertions (a lesson from the zsum adapter work).
    """
    for var in (*_KEY_ENV_VARS, *_NLM_ENV_VARS, *_PROVIDER_CONFIG_VARS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def reload_client(tmp_path: Path) -> Iterator[TestClient]:
    """FastAPI TestClient with an isolated tmp cache_path and empty config.

    Uses patch.dict to isolate env vars so LOGIC_LOOM_CONFIG and
    LOGIC_LOOM_CACHE_PATH point at tmp paths. Calls reload_settings() before
    and after to avoid polluting the process-wide settings cache.
    """
    cfg_path = tmp_path / "config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
            # Point at a non-existent vault so Settings.load() doesn't fail on
            # path-existence checks. tmp_path itself is a valid directory.
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
        },
        clear=False,
    ):
        from logic_loom_api.deps import reload_settings

        reload_settings()
        from logic_loom_api.main import api

        with TestClient(api) as client:
            yield client
        # Restore the settings cache so subsequent tests get a clean state.
        reload_settings()


class TestUnifiedReloadIntegration:
    """One reload rewires all three consumers."""

    @pytest.mark.integration
    def test_one_reload_rewires_all_three_consumers(
        self,
        reload_client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A single POST /api/llm/providers/reload rewires Learn + zsum + node-gen.

        One reload call reflects the reloaded settings across all three
        consumer resolvers. Asserting via:
        - resolve_choice(settings, 'learn') — Learn provider
        - resolve_zsum_runtime(settings) — zsum adapter runtime
        - resolve_node_gen_runtime(settings) — node-gen adapter runtime

        The test writes a new config.toml setting llm_provider=claude-cli, then
        calls reload, then asserts all three resolvers see the new choice. Both
        binaries (claude and codex) are forced on PATH so CLI providers resolve.

        NOT asserting rewire_host_llm(...)["zsum"] or ["nodegen"] — both slots
        are [] by design (per-dispatch transient env, not a global state write).
        The meaningful signal is whether each
        consumer's resolver reflects the reloaded settings.
        """
        # Force all CLI binaries on PATH so the claude-cli choice actually resolves.
        monkeypatch.setattr(discovery.shutil, "which", lambda binary: "/usr/bin/" + binary)

        cfg_path = Path(os.environ["LOGIC_LOOM_CONFIG"])

        # ── Step 1: baseline — no provider configured ────────────────────────
        # Verify that before reload, no provider resolves (env keys scrubbed by
        # autouse fixture, config file does not exist yet).
        from logic_loom_api.deps import get_settings

        pre_settings = get_settings()
        pre_learn = resolve_choice(pre_settings, "learn")
        pre_zsum = zsum_adapter.resolve_zsum_runtime(pre_settings)
        pre_nodegen = resolve_node_gen_runtime(pre_settings)

        # With no config file and no env keys, everything should be None.
        # (If any one resolves, that's a baseline-isolation problem, not a
        # reload-rewiring failure — document it as such.)
        assert pre_learn is None, (
            "Baseline: no provider must be configured before writing the config "
            f"(learn resolved: {pre_learn})"
        )
        assert pre_zsum is None, (
            "Baseline: no zsum runtime must be produced before config is written "
            f"(zsum resolved: {pre_zsum})"
        )
        assert pre_nodegen is None, (
            "Baseline: no node-gen runtime must be produced before config is written "
            f"(nodegen resolved: {pre_nodegen})"
        )

        # ── Step 2: write a new config with llm_provider=claude-cli ─────────
        # claude-cli routes all three consumers via the global llm_provider field.
        # It maps to:
        #   - Learn: provider "claude-cli"
        #   - zsum: provider "anthropic" (via _HOST_TO_ZSUM_PROVIDER)
        #   - node-gen: binary "claude", provider "claude-cli"
        cfg_path.write_text('llm_provider = "claude-cli"\n', encoding="utf-8")

        # ── Step 3: single POST /api/llm/providers/reload ────────────────────
        resp = reload_client.post("/api/llm/providers/reload")
        assert resp.status_code == 200, (
            f"Reload endpoint must return 200; got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("reloaded") is True, f"Response must carry reloaded=true; got {body}"

        # ── Step 4: assert all three consumers see the reloaded settings ─────
        # After reload, get_settings() must return fresh settings from the new
        # config file (reload_settings() was called inside the endpoint).
        post_settings = get_settings()

        # Sanity: the reloaded settings carry the new global provider.
        assert post_settings.llm_provider == "claude-cli", (
            f"Reloaded settings must carry llm_provider='claude-cli'; "
            f"got {post_settings.llm_provider!r}"
        )

        # Learn: resolve_choice reads the reloaded settings and must return claude-cli.
        post_learn = resolve_choice(post_settings, "learn")
        assert post_learn is not None, (
            "After reload, Learn provider must resolve to claude-cli (not None)"
        )
        assert post_learn.provider == "claude-cli", (
            f"After reload, Learn must resolve claude-cli; got {post_learn.provider!r}"
        )

        # zsum: resolve_zsum_runtime reads the reloaded settings.
        # claude-cli → anthropic in the HOST_TO_ZSUM mapping.
        post_zsum = zsum_adapter.resolve_zsum_runtime(post_settings)
        assert post_zsum is not None, (
            "After reload, resolve_zsum_runtime must return a runtime (not None)"
        )
        assert post_zsum.provider == "anthropic", (
            f"After reload, zsum runtime must have provider='anthropic' "
            f"(claude-cli → anthropic); got {post_zsum.provider!r}"
        )

        # node-gen: resolve_node_gen_runtime reads the reloaded settings.
        # claude-cli CLI path → binary "claude", provider "claude-cli".
        post_nodegen = resolve_node_gen_runtime(post_settings)
        assert post_nodegen is not None, (
            "After reload, resolve_node_gen_runtime must return a runtime (not None)"
        )
        assert post_nodegen.provider == "claude-cli", (
            f"After reload, node-gen runtime must have provider='claude-cli'; "
            f"got {post_nodegen.provider!r}"
        )
        assert post_nodegen.binary == "claude", (
            f"After reload, node-gen binary must be 'claude' (claude-cli → claude); "
            f"got {post_nodegen.binary!r}"
        )

    @pytest.mark.integration
    def test_reload_with_api_key_choice_routes_all_three(
        self,
        reload_client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reload with akms (API) choice routes all three consumers when key present.

        Second scenario: a different provider (akms API choice) also
        rewires all three consumers in one reload. Demonstrates the reload path
        is not CLI-only.
        """
        # Inject an API key so 'akms' choice resolves.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-reload-test")
        _all_on_path = lambda binary: "/usr/bin/" + binary  # noqa: E731
        monkeypatch.setattr(discovery.shutil, "which", _all_on_path)

        cfg_path = Path(os.environ["LOGIC_LOOM_CONFIG"])
        cfg_path.write_text('llm_provider = "akms"\n', encoding="utf-8")

        resp = reload_client.post("/api/llm/providers/reload")
        assert resp.status_code == 200

        from logic_loom_api.deps import get_settings

        post_settings = get_settings()
        assert post_settings.llm_provider == "akms"

        # Learn: akms → completion provider.
        post_learn = resolve_choice(post_settings, "learn")
        assert post_learn is not None
        assert post_learn.provider == "akms"

        # zsum: akms + ANTHROPIC_API_KEY → anthropic runtime with key forwarded.
        post_zsum = zsum_adapter.resolve_zsum_runtime(post_settings)
        assert post_zsum is not None
        assert post_zsum.provider == "anthropic"
        assert "ANTHROPIC_API_KEY" in post_zsum.env_overrides

        # node-gen: akms + ANTHROPIC_API_KEY → binary claude + AKMS_LLM_PROVIDER env.
        post_nodegen = resolve_node_gen_runtime(post_settings)
        assert post_nodegen is not None
        assert post_nodegen.binary == "claude"
        assert post_nodegen.env_overrides.get("AKMS_LLM_PROVIDER") == "anthropic"

    @pytest.mark.integration
    def test_reload_clearing_choice_reverts_all_consumers_to_noop(
        self,
        reload_client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reload with no choice reverts all consumers to the legacy no-op.

        If a reload writes an empty config (no llm_provider), all
        three consumers fall back to None/claude. This closes the full round-trip:
        configure → reload → verify; clear → reload → verify revert.
        """
        monkeypatch.setattr(discovery.shutil, "which", lambda binary: "/usr/bin/" + binary)

        cfg_path = Path(os.environ["LOGIC_LOOM_CONFIG"])

        # Step 1: configure + reload.
        cfg_path.write_text('llm_provider = "claude-cli"\n', encoding="utf-8")
        resp1 = reload_client.post("/api/llm/providers/reload")
        assert resp1.status_code == 200

        from logic_loom_api.deps import get_settings

        post1 = get_settings()
        assert post1.llm_provider == "claude-cli"

        # Step 2: clear config + reload.
        cfg_path.write_text("", encoding="utf-8")  # empty → no llm_provider
        resp2 = reload_client.post("/api/llm/providers/reload")
        assert resp2.status_code == 200

        post2 = get_settings()
        assert post2.llm_provider is None, (
            f"After clearing config, llm_provider must be None; got {post2.llm_provider!r}"
        )

        # All three consumers must revert to None/claude (no-op).
        # Keys were scrubbed by the autouse fixture, so no auto-completion either.
        monkeypatch.setattr(discovery.shutil, "which", lambda _name: None)

        post_learn = resolve_choice(post2, "learn")
        assert post_learn is None, (
            "After clearing config, Learn must revert to None (no-op)"
        )

        post_zsum = zsum_adapter.resolve_zsum_runtime(post2)
        assert post_zsum is None, (
            "After clearing config, zsum must revert to None (YAML path unchanged)"
        )

        post_nodegen = resolve_node_gen_runtime(post2)
        assert post_nodegen is None, (
            "After clearing config, node-gen must revert to None (claude fallback)"
        )
