"""Tests for the cross-consumer host-LLM routing precedence matrix.

Proves one host-LLM choice routes correctly across all three consumers (Learn,
zsum, node-gen), asserted **through each adapter's resolved runtime**, NOT through
the ``rewire_host_llm`` ``"zsum"``/``"nodegen"`` response slots (both ``[]`` by
design: dispatch env is applied per run, not as global state).

Covers:
  1. Routing precedence matrix per consumer (per-consumer override vs global
     ``llm_provider`` vs legacy ``learn_llm_provider`` alias), asserted via each
     consumer's resolved runtime.
  2. No-regression: each consumer with no choice keeps its legacy behaviour (zsum
     YAML path unchanged; node-gen claude fallback).

Resolvers under test:
  - Learn:    host_llm.routing.resolve_choice(settings, "learn")
  - zsum:     adapters/zsum_adapter.resolve_zsum_runtime(settings)
  - node-gen: akms_dispatch.resolve_node_gen_runtime(settings) /
              require_node_gen_binary(settings)

This file passes in a core (no-akms) env without skip-gating: the resolvers
degrade to ``None`` when the akms extras are absent and the host→zsum provider
mapping is a pure dict. This dev env has all extras, so the akms-API paths are
exercised live.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import logic_loom_api.host_llm.discovery as discovery
from logic_loom_api.adapters import zsum_adapter
from logic_loom_api.akms_dispatch import resolve_node_gen_runtime, require_node_gen_binary
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


@pytest.fixture(autouse=True)
def _scrub_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove completion-key + NLM env vars so resolution is deterministic.

    Without this, a CI-exported ANTHROPIC_API_KEY would flip the akms
    auto-path and false-FAIL the no-choice cases (a lesson from the zsum adapter work).
    """
    for var in (*_KEY_ENV_VARS, *_NLM_ENV_VARS):
        monkeypatch.delenv(var, raising=False)


def _settings(**overrides: object) -> Settings:
    """Minimal Settings with all provider fields defaulted to None."""
    base: dict[str, object] = {
        "nodegen_llm_provider": None,
        "zsum_llm_provider": None,
        "llm_provider": None,
        "learn_llm_provider": None,
        "learn_llm_custom_clis": {},
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _settings_ns(**overrides: object) -> SimpleNamespace:
    """Minimal duck-typed Settings stub (SimpleNamespace) for resolver tests.

    resolve_choice reads all provider fields via getattr, so a SimpleNamespace
    works as a lightweight stand-in when we don't need effective_llm_provider().
    """
    base: dict[str, object] = {
        "nodegen_llm_provider": None,
        "zsum_llm_provider": None,
        "llm_provider": None,
        "learn_llm_provider": None,
        "learn_llm_custom_clis": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _all_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every binary resolve on PATH (so CLI providers are 'available')."""
    monkeypatch.setattr(discovery.shutil, "which", lambda binary: "/usr/bin/" + binary)


class TestCrossConsumerRoutingMatrix:
    """Cross-consumer routing precedence and no-choice no-regression."""

    # ------------------------------------------------------------------ #
    # Precedence — Precedence matrix: per-consumer override wins over global
    # ------------------------------------------------------------------ #

    @pytest.mark.integration
    def test_per_consumer_override_wins_over_global_for_learn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per-consumer learn override beats global llm_provider for Learn.

        Precedence: the per-consumer override field (learn_llm_provider) wins over the
        global llm_provider for the Learn consumer. Asserted via resolve_choice.
        """
        # Global says codex-cli; per-consumer Learn override says claude-cli.
        # claude must be on PATH so the explicit CLI choice resolves.
        _all_on_path(monkeypatch)
        settings = _settings_ns(
            learn_llm_provider="claude-cli",
            llm_provider="codex-cli",
        )
        result = resolve_choice(settings, "learn")
        assert result is not None, "Expected a resolved provider for learn consumer"
        assert result.provider == "claude-cli", (
            f"Per-consumer learn override must win over global llm_provider; "
            f"got {result.provider!r}"
        )

    @pytest.mark.integration
    def test_per_consumer_override_wins_over_global_for_zsum(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per-consumer zsum override beats global llm_provider for zsum.

        Precedence: zsum_llm_provider wins over llm_provider. Asserted via
        resolve_zsum_runtime — the zsum adapter's public resolver.
        """
        # Global says codex-cli; per-consumer zsum override says claude-cli.
        # claude-cli maps to zsum's 'anthropic' provider.
        _all_on_path(monkeypatch)
        settings = _settings(
            zsum_llm_provider="claude-cli",
            llm_provider="codex-cli",
        )
        runtime = zsum_adapter.resolve_zsum_runtime(settings)
        assert runtime is not None, (
            "Expected a resolved zsum runtime when zsum_llm_provider=claude-cli"
        )
        assert runtime.provider == "anthropic", (
            f"Per-consumer zsum override (claude-cli → anthropic) must win over "
            f"global codex-cli; got {runtime.provider!r}"
        )

    @pytest.mark.integration
    def test_per_consumer_override_wins_over_global_for_nodegen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per-consumer nodegen override beats global llm_provider for node-gen.

        Precedence: nodegen_llm_provider wins over llm_provider. Asserted via
        resolve_node_gen_runtime.
        """
        # Global says claude-cli; per-consumer nodegen override says codex-cli.
        # Both are on PATH.
        _all_on_path(monkeypatch)
        settings = _settings_ns(
            nodegen_llm_provider="codex-cli",
            llm_provider="claude-cli",
        )
        runtime = resolve_node_gen_runtime(settings)
        assert runtime is not None, (
            "Expected a resolved node-gen runtime when nodegen_llm_provider=codex-cli"
        )
        assert runtime.binary == "codex", (
            f"Per-consumer nodegen override (codex-cli → codex) must win over "
            f"global claude-cli; got {runtime.binary!r}"
        )
        assert runtime.provider == "codex-cli"

    # ------------------------------------------------------------------ #
    # Precedence — Global llm_provider applies when no per-consumer override is set
    # ------------------------------------------------------------------ #

    @pytest.mark.integration
    def test_global_llm_provider_applies_to_all_consumers_when_no_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Global llm_provider routes all three consumers when no per-consumer override.

        Precedence: with only llm_provider set (no per-consumer overrides), all three
        consumers resolve to the global choice. This is the key cross-consumer
        property — one field controls all three.
        """
        _all_on_path(monkeypatch)
        settings_ns = _settings_ns(llm_provider="claude-cli")
        settings_full = _settings(llm_provider="claude-cli")

        # Learn: resolves via resolve_choice
        learn_result = resolve_choice(settings_ns, "learn")
        assert learn_result is not None
        assert learn_result.provider == "claude-cli", (
            f"Global llm_provider must apply to learn; got {learn_result.provider!r}"
        )

        # zsum: resolves via resolve_zsum_runtime (claude-cli → anthropic)
        zsum_runtime = zsum_adapter.resolve_zsum_runtime(settings_full)
        assert zsum_runtime is not None, (
            "Global llm_provider=claude-cli must produce a zsum runtime"
        )
        assert zsum_runtime.provider == "anthropic", (
            f"claude-cli maps to anthropic in zsum; got {zsum_runtime.provider!r}"
        )

        # node-gen: resolves via resolve_node_gen_runtime (codex-cli → codex binary)
        nodegen_runtime = resolve_node_gen_runtime(settings_ns)
        assert nodegen_runtime is not None, (
            "Global llm_provider=claude-cli must produce a node-gen runtime"
        )
        assert nodegen_runtime.binary == "claude", (
            f"claude-cli maps to claude binary in node-gen; got {nodegen_runtime.binary!r}"
        )
        assert nodegen_runtime.provider == "claude-cli"

    # ------------------------------------------------------------------ #
    # Precedence — Legacy learn_llm_provider alias is the final fallback
    # ------------------------------------------------------------------ #

    @pytest.mark.integration
    def test_legacy_learn_llm_provider_alias_routes_all_consumers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The legacy learn_llm_provider alias routes all consumers when both
        llm_provider and per-consumer fields are unset (legacy-alias back-compat).

        Precedence: the precedence chain (per-consumer override → global llm_provider →
        legacy learn_llm_provider alias) is complete. Setting only
        learn_llm_provider reaches all three consumers.
        """
        _all_on_path(monkeypatch)
        settings_ns = _settings_ns(learn_llm_provider="claude-cli")
        settings_full = _settings(learn_llm_provider="claude-cli")

        # Learn: the per-consumer "override" field for learn IS learn_llm_provider,
        # so this also serves as the override for the learn consumer.
        learn_result = resolve_choice(settings_ns, "learn")
        assert learn_result is not None
        assert learn_result.provider == "claude-cli"

        # zsum: per-consumer zsum override is None, global llm_provider is None,
        # so the legacy alias learn_llm_provider fires.
        zsum_runtime = zsum_adapter.resolve_zsum_runtime(settings_full)
        assert zsum_runtime is not None, (
            "Legacy learn_llm_provider alias must reach zsum when no override/global set"
        )
        assert zsum_runtime.provider == "anthropic", (
            f"claude-cli → anthropic for zsum; got {zsum_runtime.provider!r}"
        )

        # node-gen: same alias fires for nodegen.
        nodegen_runtime = resolve_node_gen_runtime(settings_ns)
        assert nodegen_runtime is not None, (
            "Legacy learn_llm_provider alias must reach nodegen when no override/global set"
        )
        assert nodegen_runtime.provider == "claude-cli"

    # ------------------------------------------------------------------ #
    # Precedence — API-key choice (akms) routes all consumers
    # ------------------------------------------------------------------ #

    @pytest.mark.integration
    def test_akms_api_choice_routes_all_three_consumers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Global akms choice routes all three consumers when an API key is present.

        Precedence: the API (akms/completion) route is the same cross-consumer property
        as the CLI route — one global choice propagates everywhere.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        _all_on_path(monkeypatch)

        settings_ns = _settings_ns(llm_provider="akms")
        settings_full = _settings(llm_provider="akms")

        # Learn: akms → completion provider
        learn_result = resolve_choice(settings_ns, "learn")
        assert learn_result is not None
        assert learn_result.provider == "akms", (
            f"akms choice must resolve for learn; got {learn_result.provider!r}"
        )

        # zsum: akms → anthropic (via _HOST_TO_ZSUM_PROVIDER mapping)
        zsum_runtime = zsum_adapter.resolve_zsum_runtime(settings_full)
        assert zsum_runtime is not None, (
            "akms choice + ANTHROPIC_API_KEY must produce a zsum runtime"
        )
        assert zsum_runtime.provider == "anthropic", (
            f"akms maps to anthropic for zsum; got {zsum_runtime.provider!r}"
        )
        # Key env is forwarded to the subprocess (the bridge sets it).
        assert "ANTHROPIC_API_KEY" in zsum_runtime.env_overrides, (
            "ANTHROPIC_API_KEY must be in zsum env_overrides for akms choice"
        )

        # node-gen: akms → binary stays claude but env is set
        nodegen_runtime = resolve_node_gen_runtime(settings_ns)
        assert nodegen_runtime is not None, (
            "akms choice + ANTHROPIC_API_KEY must produce a node-gen runtime"
        )
        assert nodegen_runtime.binary == "claude", (
            "akms API path dispatches via claude agent; binary must be claude"
        )
        assert nodegen_runtime.env_overrides.get("AKMS_LLM_PROVIDER") == "anthropic", (
            "AKMS_LLM_PROVIDER must be set to anthropic for akms choice"
        )
        assert "ANTHROPIC_API_KEY" in nodegen_runtime.env_overrides

    # ------------------------------------------------------------------ #
    # No-regression — no choice is a legacy no-op for every consumer
    # ------------------------------------------------------------------ #

    @pytest.mark.integration
    def test_no_choice_is_pre_pr2_noop_per_consumer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no host-LLM choice set, each consumer keeps its legacy behaviour.

        - Learn: resolve_choice returns None (no provider, no auto-resolution
          without a key or NLM).
        - zsum: resolve_zsum_runtime returns None (YAML-unchanged path).
        - node-gen: resolve_node_gen_runtime returns None; require_node_gen_binary
          returns 'claude' (claude fallback).
        """
        # No CLI binaries on PATH either (nlm absent → no auto-nlm).
        monkeypatch.setattr(discovery.shutil, "which", lambda _name: None)

        settings_ns = _settings_ns()  # all None
        settings_full = _settings()

        # Learn: nothing configured → None.
        learn_result = resolve_choice(settings_ns, "learn")
        assert learn_result is None, (
            "No choice must leave Learn provider as None (legacy behaviour)"
        )

        # zsum: None → adapter does not inject any override (YAML path regression).
        zsum_runtime = zsum_adapter.resolve_zsum_runtime(settings_full)
        assert zsum_runtime is None, (
            "No choice must leave resolve_zsum_runtime as None (YAML path unchanged)"
        )

        # node-gen: None → runtime None; gate returns 'claude' (claude-fallback regression).
        nodegen_runtime = resolve_node_gen_runtime(settings_ns)
        assert nodegen_runtime is None, (
            "No choice must leave resolve_node_gen_runtime as None (claude fallback)"
        )
        binary = require_node_gen_binary(settings_ns)
        assert binary == "claude", (
            f"No choice must return 'claude' from require_node_gen_binary; got {binary!r}"
        )

    # ------------------------------------------------------------------ #
    # Precedence — Explicit per-consumer fields are truly independent (isolation)
    # ------------------------------------------------------------------ #

    @pytest.mark.integration
    def test_per_consumer_overrides_are_independent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each consumer's per-consumer field is independent from the others.

        Precedence: setting nodegen_llm_provider does NOT affect the zsum or learn
        consumer (and vice versa). This is the isolation property of the
        three-field design.
        """
        _all_on_path(monkeypatch)
        # nodegen gets codex-cli; zsum and learn fall back to global (claude-cli).
        settings_full = _settings(
            nodegen_llm_provider="codex-cli",
            llm_provider="claude-cli",
        )
        settings_ns = _settings_ns(
            nodegen_llm_provider="codex-cli",
            llm_provider="claude-cli",
        )

        # zsum: uses global claude-cli (not nodegen's codex-cli).
        zsum_runtime = zsum_adapter.resolve_zsum_runtime(settings_full)
        assert zsum_runtime is not None
        assert zsum_runtime.provider == "anthropic", (
            f"zsum must use global claude-cli (→ anthropic), not nodegen's codex-cli; "
            f"got {zsum_runtime.provider!r}"
        )

        # learn: uses global claude-cli.
        learn_result = resolve_choice(settings_ns, "learn")
        assert learn_result is not None
        assert learn_result.provider == "claude-cli", (
            f"learn must use global claude-cli, not nodegen's codex-cli; "
            f"got {learn_result.provider!r}"
        )

        # node-gen: uses its own codex-cli override.
        nodegen_runtime = resolve_node_gen_runtime(settings_ns)
        assert nodegen_runtime is not None
        assert nodegen_runtime.binary == "codex", (
            f"node-gen must use per-consumer codex-cli; got {nodegen_runtime.binary!r}"
        )

    # ------------------------------------------------------------------ #
    # Precedence — require_node_gen_binary agrees with resolve_node_gen_runtime
    # ------------------------------------------------------------------ #

    @pytest.mark.integration
    def test_gate_and_resolver_agree_on_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """require_node_gen_binary returns the same binary the resolver would use.

        Precedence: the gate and the resolver agree by construction. This pins the
        contract so the gate and dispatch can never diverge on which binary
        gets used.
        """
        _all_on_path(monkeypatch)
        settings_ns = _settings_ns(nodegen_llm_provider="codex-cli")

        runtime = resolve_node_gen_runtime(settings_ns)
        assert runtime is not None
        resolver_binary = runtime.binary

        gate_binary = require_node_gen_binary(settings_ns)
        assert gate_binary == resolver_binary, (
            f"Gate ({gate_binary!r}) and resolver ({resolver_binary!r}) must agree "
            f"on the dispatch binary"
        )
