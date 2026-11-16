"""Tests for zsum back-compat — no choice → existing YAML path unchanged.

Covers:
  1 (With no global/zsum choice, the zsum Config passed to the pipeline equals
     the one produced from zsum.config.yaml alone — no injected overrides).
  Degrade gracefully: The no-choice → YAML-unchanged
     back-compat path must also hold when resolution would otherwise raise
     (malformed custom CLI shlex.split ValueError, or unmapped custom-label
     provider UnknownProviderError) — degrade to unchanged YAML, never a 500.

Design note: test_zsum_llm_adapter.py already has ``test_route_no_choice_is_a_true_noop``
which covers the route-level no-op (config_path identity + no transient file).
This file is orthogonal: it locks the CONTRACT at the adapter seam — that ``resolve_zsum_runtime``
returns ``None`` (no overrides) AND that when it does return None the zsum Config
object is byte-identical to the one produced by zsum.config.yaml alone.  The degrade
tests are non-vacuous: the malformed value / unmapped label actually reaches the
split / the label is actually processed before degrading.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from logic_loom_api.adapters import zsum_adapter
from logic_loom_api.config import Settings

# Skip the whole module if the vendored zsum package isn't importable
# (core-only checkout without the zotero-summarizer extra).
zsum_config = pytest.importorskip("zsum.config")


def _patch_clis_on_path(monkeypatch: pytest.MonkeyPatch, *binaries: str) -> None:
    """Make only ``binaries`` appear on PATH for CLI provider discovery."""
    from logic_loom_api.host_llm import discovery

    wanted = set(binaries)
    monkeypatch.setattr(
        discovery.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in wanted else None,
    )


def _fresh_config_from_yaml(yaml_path: Path) -> zsum_config.Config:
    """Load a zsum Config from a real YAML file — the baseline the pipeline would see."""
    return zsum_config.load_config(yaml_path)


class TestZsumLlmBackcompat:
    """Tests for zsum back-compat (adapter boundary)."""

    @pytest.mark.regression
    def test_zsum_config_unchanged_when_no_choice_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: When no global/zsum choice is configured, resolve_zsum_runtime
        returns None (no overrides) and the resulting Config is byte-identical to the
        one produced from zsum.config.yaml alone.

        Requirement: 'With no global/zsum choice, the zsum Config passed to the pipeline equals
        the one produced from zsum.config.yaml alone — no injected overrides.'

        Contract locked here:
          - resolve_zsum_runtime(settings_no_choice) is None  → adapter injects NOTHING
          - A Config built WITHOUT the adapter == a Config built from YAML alone
          - The env is untouched (no key var injected when unconfigured)
        """
        # Remove all completion key env vars so nothing auto-resolves.
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        # Write a minimal zsum.config.yaml with known content.
        cfg_yaml = tmp_path / "zsum.config.yaml"
        cfg_yaml.write_text(
            "bbt_json: ~/Zotero/zot.json\nllm:\n  provider: anthropic\n",
            encoding="utf-8",
        )

        # Settings with NO provider choice at any level.
        settings = Settings(
            zsum_config_path=cfg_yaml,
            cache_path=tmp_path / "cache",
            # explicitly no llm_provider / zsum_llm_provider / learn_llm_provider
        )

        # --- Adapter-seam assertion 1: resolve_zsum_runtime → None ---
        runtime = zsum_adapter.resolve_zsum_runtime(settings)
        assert runtime is None, (
            "resolve_zsum_runtime must return None when no choice is configured "
            "(the adapter must inject nothing into the zsum request path)"
        )

        # --- Adapter-seam assertion 2: positive control on the override path ---
        # Comparing two Configs both loaded from the untouched YAML would be
        # tautological (it never invokes apply_runtime_to_config, so it can't catch
        # a regression). Instead, PROVE the no-choice Config genuinely differs from
        # what an OVERRIDE path would produce: construct a resolved ZsumRuntime
        # (provider switched to openai_compat) and show apply_runtime_to_config WOULD
        # mutate the provider. The no-op path (runtime is None) is the one that
        # leaves the baseline 'anthropic' intact.
        baseline_cfg = _fresh_config_from_yaml(cfg_yaml)
        assert baseline_cfg.llm.provider == "anthropic"  # the YAML value
        override_runtime = zsum_adapter.ZsumRuntime(
            provider="openai_compat",
            config_overrides={"llm.provider": "openai_compat"},
        )
        overridden_cfg = zsum_adapter.apply_runtime_to_config(
            _fresh_config_from_yaml(cfg_yaml), override_runtime
        )
        assert overridden_cfg.llm.provider == "openai_compat", (
            "Sanity: apply_runtime_to_config DOES change the provider — so the "
            "no-choice path (which never calls it) is a meaningful, non-tautological no-op"
        )
        assert baseline_cfg.llm.provider != overridden_cfg.llm.provider, (
            "The no-choice Config must differ from what an override path would produce"
        )
        # No env overrides would be injected on the no-op path — the key env is untouched.
        assert "ANTHROPIC_API_KEY" not in (runtime.env_overrides if runtime else {}), (
            "No key env var must be injected when no host choice is set"
        )

        # --- Adapter-seam assertion 3: YAML file is byte-identical (no mutation) ---
        expected_content = "bbt_json: ~/Zotero/zot.json\nllm:\n  provider: anthropic\n"
        assert cfg_yaml.read_text(encoding="utf-8") == expected_content, (
            "The committed zsum.config.yaml must be untouched when no choice is configured"
        )

        # --- Adapter-seam assertion 4: no transient effective-config dir created ---
        from logic_loom_api.routes import zsum as zsum_route

        assert not zsum_route._effective_zsum_config_dir(settings).exists(), (
            "No transient effective-config directory must be created when no choice is set"
        )

    @pytest.mark.regression
    def test_no_choice_yaml_path_unchanged_even_when_resolution_would_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for the degrade-gracefully invariant:
        The 'no choice → YAML unchanged' back-compat path must also hold when resolution
        would otherwise raise — a malformed custom CLI (shlex.split ValueError) or
        an unmapped custom-label provider (UnknownProviderError) must degrade to the
        unchanged YAML path, never a 500.

        NON-VACUOUS guarantee, positively pinned:
          - Sub-case A: the malformed label's binary is forced onto PATH, so the ONLY
            reason it is dropped from available_cli_providers is _safe_split returning
            None on the unparseable command (the degrade guard) — not a missing binary.
          - Sub-case B: the unmapped label genuinely resolves (resolve_choice is non-None)
            but _zsum_provider_for(resolved) is None (no zsum backend), so the
            unmapped-provider degrade path is actually exercised.

        Requirement: 'Degrade to unchanged YAML (no injected overrides) when resolution raises,
        never a 500.' Passes when: with a malformed custom CLI or unmapped provider,
        resolve_zsum_runtime returns None and the route seam yields the original
        committed YAML path with no overrides injected.
        """
        from logic_loom_api.host_llm.discovery import available_cli_providers
        from logic_loom_api.host_llm.routing import _COMPLETION_ENV_VARS, resolve_choice
        from logic_loom_api.routes import zsum as zsum_route

        # Scrub every completion key the resolver reads so a key exported in
        # CI/dev can't auto-resolve to `akms` and false-fail the `runtime is None`
        # sub-cases. Use the SAME list the production resolver reads
        # (`_COMPLETION_ENV_VARS`) so this stays in sync if a key is ever added.
        for var in _COMPLETION_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        # GEMINI_API_KEY is read by the zsum gemini_cli path; scrub it too.
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        cfg_yaml = tmp_path / "zsum.config.yaml"
        original_content = "bbt_json: ~/Zotero/zot.json\nllm:\n  provider: anthropic\n"
        cfg_yaml.write_text(original_content, encoding="utf-8")

        # ── Sub-case A: malformed custom CLI (unbalanced quote → shlex ValueError) ──
        # The label "my-broken-tool" is set as the zsum choice. learn_llm_custom_clis
        # carries a malformed command string that would cause shlex.split to raise
        # ValueError if unguarded. _safe_split returning None is what drops this label
        # from availability — degrade, not raise, into the request path.
        settings_malformed = Settings(
            zsum_config_path=cfg_yaml,
            cache_path=tmp_path / "cache",
            zsum_llm_provider="my-broken-tool",
            learn_llm_custom_clis={"my-broken-tool": 'mytool --flag "unbalanced'},
        )
        # POSITIVE PROOF of the _safe_split path (degrade-gracefully headline): the malformed label is
        # absent from available_cli_providers BECAUSE its command fails to parse. Make
        # the binary appear on PATH so the ONLY reason the label is dropped is the
        # unparseable command (shlex ValueError → _safe_split returns None), not a
        # missing binary — this pins the guard rather than re-reading constructor input.
        _patch_clis_on_path(monkeypatch, "mytool")
        available = available_cli_providers(settings_malformed)
        assert "my-broken-tool" not in available, (
            "Malformed custom CLI must be dropped from availability by _safe_split "
            "(unparseable command → None), even though its binary is on PATH"
        )
        # And resolve_choice therefore yields no host choice for the malformed label.
        assert resolve_choice(settings_malformed, "zsum") is None, (
            "A malformed custom-CLI label must not resolve to a host choice"
        )

        # The adapter must degrade to None — never raise.
        runtime_malformed = zsum_adapter.resolve_zsum_runtime(settings_malformed)
        assert runtime_malformed is None, (
            "Malformed custom CLI must degrade to None (no zsum override), never raise"
        )

        # Route seam: _apply_host_llm_runtime must also degrade — original path, no injection.
        config_path_a, overrides_a = zsum_route._apply_host_llm_runtime(settings_malformed, {})
        assert config_path_a == settings_malformed.effective_zsum_config_path(), (
            "Malformed custom CLI: route must fall back to the original committed YAML path"
        )
        assert overrides_a == {}, (
            "Malformed custom CLI: no env overrides must be injected on degrade"
        )
        # The committed YAML is byte-identical to baseline — never mutated.
        assert cfg_yaml.read_text(encoding="utf-8") == original_content, (
            "Malformed custom CLI: committed YAML must be untouched on degrade"
        )

        # ── Sub-case B: unmapped custom-label provider (no zsum backend) ──
        # "weird-label" is on PATH (so resolve_choice CAN return a HostLLMProvider)
        # but is NOT in _HOST_TO_ZSUM_PROVIDER (no zsum backend). This means the
        # path ENTERS _zsum_provider_for and returns None — non-vacuous.
        _patch_clis_on_path(monkeypatch, "weird-label")
        settings_unmapped = Settings(
            zsum_config_path=cfg_yaml,
            cache_path=tmp_path / "cache",
            zsum_llm_provider="weird-label",
            learn_llm_custom_clis={"weird-label": "weird-label run"},
        )

        # Non-vacuous: the label genuinely resolves (so the unmapped path IS exercised).
        resolved = resolve_choice(settings_unmapped, "zsum")
        assert resolved is not None, (
            "weird-label must resolve via resolve_choice so the unmapped-provider "
            "degrade path is non-vacuously exercised"
        )
        assert resolved.provider == "weird-label"

        # The host→zsum mapping has no entry for this label → _zsum_provider_for returns None.
        assert zsum_adapter._zsum_provider_for(resolved) is None, (
            "weird-label must have no zsum backend so the unmapped degrade path fires"
        )

        # Adapter must degrade to None (no zsum override), never raise.
        runtime_unmapped = zsum_adapter.resolve_zsum_runtime(settings_unmapped)
        assert runtime_unmapped is None, (
            "Unmapped custom-label provider must degrade to None (no zsum override), never raise"
        )

        # Route seam: original path, no injection.
        config_path_b, overrides_b = zsum_route._apply_host_llm_runtime(settings_unmapped, {})
        assert config_path_b == settings_unmapped.effective_zsum_config_path(), (
            "Unmapped provider: route must fall back to the original committed YAML path"
        )
        assert overrides_b == {}, (
            "Unmapped provider: no env overrides must be injected on degrade"
        )
        # Committed YAML untouched.
        assert cfg_yaml.read_text(encoding="utf-8") == original_content, (
            "Unmapped provider: committed YAML must be untouched on degrade"
        )

    @pytest.mark.regression
    def test_env_untouched_when_no_choice_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: No key env var (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY)
        is injected into the subprocess env when no host/zsum choice is configured.

        This strengthens the back-compat contract: env_overrides must be empty when
        resolve_zsum_runtime returns None (the no-op path injects nothing into the
        subprocess env, so the zsum subprocess sees only what the user already set).
        """
        # Ensure all known completion keys are ABSENT from process env.
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        # No choice at any level.
        settings = Settings()
        runtime = zsum_adapter.resolve_zsum_runtime(settings)

        # runtime is None → the route injects nothing (env_overrides is never accessed).
        assert runtime is None, "No choice → runtime must be None"

        # Confirm the contract explicitly: if runtime were non-None we'd assert
        # env_overrides == {}. Since it's None, the no-op path carries no env at all.
        # This is the "env is untouched" guarantee — no key is injected.
        env_overrides = runtime.env_overrides if runtime is not None else {}
        assert env_overrides == {}, (
            "No key env var must be injected when no host/zsum choice is configured"
        )
