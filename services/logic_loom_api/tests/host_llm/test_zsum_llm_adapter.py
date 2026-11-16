"""Tests for adapters/zsum_adapter.py: map host choice -> zsum provider/model/key.

Covers:
  1 (Global choice codex-cli => zsum dispatches with provider='codex_exec'),
  2 (zsum_llm_provider override beats the global choice).

Degrade-gracefully:
  - Malformed custom CLI must not raise into zsum path (use _safe_split, as host_llm discovery does).
  - route_for's UnknownProviderError for unmapped-but-resolvable provider must degrade to YAML/None, never 500.

All assertions go through the constructed zsum ``Config`` (or the ``ZsumRuntime``
the adapter builds it from) — no network, no subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from logic_loom_api.adapters import zsum_adapter
from logic_loom_api.config import Settings
from logic_loom_api.host_llm.routing import HostLLMProvider

# The zsum Config surface is the public override path (vendored trees are never
# edited). Skip the
# whole module cleanly if the vendored package isn't importable (core-only checkout).
zsum_config = pytest.importorskip("zsum.config")


def _patch_clis_on_path(monkeypatch: pytest.MonkeyPatch, *binaries: str) -> None:
    """Make only ``binaries`` appear on PATH for CLI provider discovery.

    ``available_cli_providers`` (host_llm/discovery.py) gates a CLI provider on
    ``shutil.which(binary)``. Patching the discovery module's ``shutil.which``
    lets us resolve ``claude-cli`` / ``codex-cli`` deterministically without a
    real binary installed.
    """
    from logic_loom_api.host_llm import discovery

    wanted = set(binaries)
    monkeypatch.setattr(
        discovery.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in wanted else None,
    )


def _effective_config(runtime: zsum_adapter.ZsumRuntime) -> zsum_config.Config:
    """Apply a runtime onto a fresh zsum ``Config`` and return it (the override path)."""
    return zsum_adapter.apply_runtime_to_config(zsum_config.Config(), runtime)


class TestZsumLlmAdapter:
    """Tests for the zsum adapter — map host choice to zsum runtime."""

    @pytest.mark.integration
    def test_provider_mapping_matrix_akms_to_anthropic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: Host provider 'akms' / 'claude-cli' maps to zsum provider='anthropic'.

        Requirement: 'Each host provider maps to the right zsum string.'
        Passes when: resolve_zsum_runtime(settings with akms/claude-cli choice) returns
        provider='anthropic' in the effective Config override.
        """
        # akms resolves via a completion API key in env (akms completion path).
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        runtime_akms = zsum_adapter.resolve_zsum_runtime(Settings(zsum_llm_provider="akms"))
        assert runtime_akms is not None
        assert runtime_akms.provider == "anthropic"
        assert _effective_config(runtime_akms).llm.provider == "anthropic"
        # The completion key is forwarded for the zsum subprocess.
        assert runtime_akms.env_overrides.get("ANTHROPIC_API_KEY") == "sk-test-key"
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        # claude-cli resolves when the `claude` binary is on PATH.
        _patch_clis_on_path(monkeypatch, "claude")
        runtime_cli = zsum_adapter.resolve_zsum_runtime(Settings(zsum_llm_provider="claude-cli"))
        assert runtime_cli is not None
        assert runtime_cli.provider == "anthropic"
        assert _effective_config(runtime_cli).llm.provider == "anthropic"

    @pytest.mark.integration
    def test_openai_maps_to_openai_compat_but_is_not_routing_resolvable_yet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: 'openai' maps to 'openai_compat' (dict level) AND is honestly forward-looking.

        Requirement: 'Each host provider maps to the right zsum string' — but the OpenAI API
        family is NOT resolvable through host_llm.routing today (it resolves only
        akms/nlm/CLIs/custom labels). This test asserts both: (a) the mapping is in
        place, and (b) resolve_zsum_runtime(Settings(zsum_llm_provider="openai"))
        does NOT yet return openai_compat — so the row isn't mistaken for working
        end-to-end. Keep the mapping, but don't let a vacuous synthetic-HostLLMProvider
        matrix test give false confidence.
        """
        # (a) The mapping itself is correct at the dict / _zsum_provider_for level.
        assert zsum_adapter._HOST_TO_ZSUM_PROVIDER["openai"] == "openai_compat"
        assert zsum_adapter._zsum_provider_for(HostLLMProvider(provider="openai")) == "openai_compat"

        # (b) HONEST end-to-end: 'openai' is not a routing-resolvable label today,
        # so the real resolver does NOT produce openai_compat. With no completion
        # key in env it resolves to nothing; the row activates only when host_llm
        # routing learns the 'openai' family label.
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        runtime = zsum_adapter.resolve_zsum_runtime(Settings(zsum_llm_provider="openai"))
        assert runtime is None or runtime.provider != "openai_compat"

    @pytest.mark.integration
    def test_provider_mapping_matrix_codex_to_codex_exec(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: Host provider 'codex-cli' maps to zsum provider='codex_exec'.

        Requirement: 'Global choice codex-cli => zsum dispatches with provider=codex_exec (asserted
        via the constructed Config, no network).'
        Passes when: resolve_zsum_runtime(settings with codex-cli choice) returns
        provider='codex_exec' in the effective Config override.
        """
        _patch_clis_on_path(monkeypatch, "codex")
        # Global choice (llm_provider), not the per-consumer override — exactly case 1.
        runtime = zsum_adapter.resolve_zsum_runtime(Settings(llm_provider="codex-cli"))
        assert runtime is not None
        assert runtime.provider == "codex_exec"
        cfg = _effective_config(runtime)
        assert cfg.llm.provider == "codex_exec"
        # codex_exec is an agent CLI — zsum auto-selects its model, none injected.
        assert runtime.model is None
        assert "llm.codex_exec.model" not in runtime.config_overrides
        # No API key env is injected for an agent-CLI provider.
        assert runtime.env_overrides == {}

    @pytest.mark.integration
    def test_google_maps_to_gemini_cli_but_is_not_routing_resolvable_yet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: 'google' maps to 'gemini_cli' (dict level) AND is honestly forward-looking.

        Requirement: 'Each host provider maps to the right zsum string' — but the google /
        gemini family is NOT resolvable through host_llm.routing today. Asserts both
        the mapping and that resolve_zsum_runtime(Settings(zsum_llm_provider="google"))
        does NOT yet return gemini_cli (no false confidence).
        """
        # (a) The mapping itself is correct at the dict / _zsum_provider_for level.
        assert zsum_adapter._HOST_TO_ZSUM_PROVIDER["google"] == "gemini_cli"
        assert zsum_adapter._zsum_provider_for(HostLLMProvider(provider="google")) == "gemini_cli"

        # (b) HONEST end-to-end: 'google' is not a routing-resolvable label today.
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        runtime = zsum_adapter.resolve_zsum_runtime(Settings(zsum_llm_provider="google"))
        assert runtime is None or runtime.provider != "gemini_cli"

    @pytest.mark.integration
    def test_per_run_override_precedence_zsum_llm_provider_beats_global(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: Per-run override zsum_llm_provider beats global host choice.

        Requirement: 'zsum_llm_provider override beats the global choice.'
        Passes when: a request with zsum_llm_provider='claude-cli' and global choice
        'codex-cli' results in provider='anthropic' (the Claude choice), not codex_exec.
        """
        # Both binaries on PATH so either choice *could* resolve — the override
        # must win on precedence, not on availability.
        _patch_clis_on_path(monkeypatch, "claude", "codex")
        settings = Settings(llm_provider="codex-cli", zsum_llm_provider="claude-cli")
        runtime = zsum_adapter.resolve_zsum_runtime(settings)
        assert runtime is not None
        # claude-cli (the per-consumer override) → anthropic, NOT codex_exec.
        assert runtime.provider == "anthropic"
        assert _effective_config(runtime).llm.provider == "anthropic"

    @pytest.mark.integration
    def test_malformed_custom_cli_does_not_raise_into_zsum_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: degrade gracefully. Malformed custom CLI must not raise into zsum request path.

        Exercises: A persisted learn_llm_custom_clis value with unbalanced quote (accepted by
        ConfigUpdate) is used as a choice, triggering zsum resolve path. The _safe_split guard
        must prevent shlex.split ValueError from escaping into the zsum dispatch.

        Passes when: resolve_zsum_runtime / zsum request path degrades the malformed CLI to
        the unchanged YAML path (or None), never raises or 500s.
        """
        # An unbalanced quote — shlex.split would raise ValueError if unguarded.
        settings = Settings(
            zsum_llm_provider="my-tool",
            learn_llm_custom_clis={"my-tool": 'mytool --flag "unbalanced'},
        )
        # Must NOT raise — degrade to "no zsum override" (None).
        runtime = zsum_adapter.resolve_zsum_runtime(settings)
        assert runtime is None

    @pytest.mark.integration
    def test_unmapped_custom_label_provider_degrades_not_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: degrade gracefully. Unmapped-but-resolvable custom provider must not 500.

        Exercises: A custom label provider (host_llm.routing.route_for's API bucket) that
        resolves but is unmapped in host_llm.model_map raises UnknownProviderError. The zsum
        adapter must guard this and degrade to YAML path / None, never a 500.

        Passes when: zsum dispatch degrades unmapped-but-resolvable provider to the unchanged
        YAML path / None, never raises into the request path or returns 500.
        """
        # A custom label whose binary IS on PATH, so resolve_choice resolves it,
        # but which is NOT in the host->zsum mapping table (no zsum backend).
        # route_for would call api_model_for('weird-llm','zsum') -> UnknownProviderError.
        _patch_clis_on_path(monkeypatch, "weird-llm")
        settings = Settings(
            zsum_llm_provider="weird-llm",
            learn_llm_custom_clis={"weird-llm": "weird-llm run"},
        )
        # Sanity: this label genuinely resolves as a host choice (so the unmapped
        # path is actually exercised, not short-circuited by a None resolution).
        from logic_loom_api.host_llm.routing import resolve_choice

        assert resolve_choice(settings, "zsum") is not None
        # The adapter must degrade to None (no zsum backend for this label), never raise.
        runtime = zsum_adapter.resolve_zsum_runtime(settings)
        assert runtime is None


class TestZsumRouteWiring:
    """The route call site (routes/zsum.py) applies the host runtime before launch.

    These exercise ``_apply_host_llm_runtime`` directly (no subprocess) — the seam
    ``submit_run`` calls before ``registry.submit``. Gate-B fix: the host override
    is materialised into a TRANSIENT effective-config file under ``cache_path`` and
    the run's ``--config`` is pointed at it; the user's committed
    ``zsum.config.yaml`` is NEVER mutated.
    """

    @pytest.mark.integration
    def test_route_materializes_transient_config_and_leaves_committed_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A resolved akms choice → transient effective config; committed YAML untouched + key forwarded."""
        from logic_loom_api.routes import zsum as zsum_route

        cfg = tmp_path / "zsum.config.yaml"
        original = "# header\nbbt_json: ~/Zotero/zot.json\n"
        cfg.write_text(original, encoding="utf-8")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-route-key")
        settings = Settings(
            zsum_config_path=cfg, cache_path=tmp_path / "cache", zsum_llm_provider="akms"
        )

        config_path, overrides = zsum_route._apply_host_llm_runtime(settings, {})

        # The run's --config points at a TRANSIENT file (not the committed one).
        assert config_path != cfg
        assert config_path.exists()
        # Committed YAML is byte-for-byte untouched (the Gate-B invariant).
        assert cfg.read_text(encoding="utf-8") == original

        # Transient effective config carries the host override, comment preserved,
        # model backfilled (blank in source) from model_map.
        effective = config_path.read_text(encoding="utf-8")
        assert "anthropic" in effective
        assert "# header" in effective
        assert "claude-sonnet-4-6" in effective
        # User's other config (bbt_json) survived into the effective copy.
        assert "bbt_json" in effective
        # Key env forwarded to the subprocess via config_overrides.
        assert overrides.get("ANTHROPIC_API_KEY") == "sk-route-key"

    @pytest.mark.integration
    def test_route_does_not_overwrite_explicit_user_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit llm.anthropic.model the user pinned survives into the effective copy."""
        from logic_loom_api.routes import zsum as zsum_route

        cfg = tmp_path / "zsum.config.yaml"
        original = "llm:\n  provider: openai_compat\n  anthropic:\n    model: my-pinned-model\n"
        cfg.write_text(original, encoding="utf-8")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-route-key")
        settings = Settings(
            zsum_config_path=cfg, cache_path=tmp_path / "cache", zsum_llm_provider="akms"
        )

        config_path, _ = zsum_route._apply_host_llm_runtime(settings, {})

        # Committed file untouched.
        assert cfg.read_text(encoding="utf-8") == original
        effective = config_path.read_text(encoding="utf-8")
        # Provider is host-owned → switched to anthropic in the effective copy.
        assert "provider: anthropic" in effective
        # User's pinned model is NOT clobbered (blank-only model write).
        assert "my-pinned-model" in effective
        assert "claude-sonnet-4-6" not in effective

    @pytest.mark.integration
    def test_route_no_choice_is_a_true_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Back-compat: no host choice → original path, nothing injected/rewritten."""
        from logic_loom_api.routes import zsum as zsum_route

        # No explicit choice AND no completion key in env → nothing resolves.
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        cfg = tmp_path / "zsum.config.yaml"
        original = "# pristine\nllm:\n  provider: anthropic\n"
        cfg.write_text(original, encoding="utf-8")
        # No llm_provider / zsum_llm_provider / learn_llm_provider set.
        settings = Settings(zsum_config_path=cfg, cache_path=tmp_path / "cache")

        config_path, overrides = zsum_route._apply_host_llm_runtime(settings, {"PRE_EXISTING": "x"})

        # The run uses the ORIGINAL committed config; nothing injected, nothing rewritten.
        assert config_path == settings.effective_zsum_config_path()
        assert overrides == {"PRE_EXISTING": "x"}
        assert cfg.read_text(encoding="utf-8") == original
        # No transient file was created (true no-op).
        assert not zsum_route._effective_zsum_config_dir(settings).exists()

    @pytest.mark.integration
    def test_route_cleared_choice_round_trip_leaves_committed_at_baseline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Gate-B regression: set choice → submit → CLEAR choice → submit ⇒ committed YAML == baseline.

        The defect was that the host override permanently mutated the committed
        zsum.config.yaml, so a cleared choice left stale host state on disk. With
        the transient-config fix the committed file must equal the original
        baseline after the whole round-trip, with no lingering host provider/model.
        """
        from logic_loom_api.routes import zsum as zsum_route

        cfg = tmp_path / "zsum.config.yaml"
        baseline = "# baseline\nbbt_json: ~/Zotero/zot.json\nllm:\n  provider: anthropic\n"
        cfg.write_text(baseline, encoding="utf-8")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-route-key")
        cache = tmp_path / "cache"

        # 1) Host choice = codex-cli (global), submit → uses a transient effective config.
        _patch_clis_on_path(monkeypatch, "codex")
        settings_set = Settings(
            zsum_config_path=cfg, cache_path=cache, llm_provider="codex-cli"
        )
        config_path_1, _ = zsum_route._apply_host_llm_runtime(settings_set, {})
        assert config_path_1 != cfg
        assert "codex_exec" in config_path_1.read_text(encoding="utf-8")
        # Committed file unchanged even after the first (set-choice) submit.
        assert cfg.read_text(encoding="utf-8") == baseline

        # 2) User CLEARS the host choice (and no completion key remains in env, so
        #    nothing auto-resolves); next submit must inject nothing and use the
        #    committed config — which is still the untouched baseline.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        settings_cleared = Settings(zsum_config_path=cfg, cache_path=cache)
        config_path_2, overrides_2 = zsum_route._apply_host_llm_runtime(settings_cleared, {})
        assert config_path_2 == settings_cleared.effective_zsum_config_path()
        assert overrides_2 == {}
        # The committed YAML equals the original baseline — no lingering codex_exec.
        assert cfg.read_text(encoding="utf-8") == baseline
        assert "codex_exec" not in cfg.read_text(encoding="utf-8")

    @pytest.mark.integration
    def test_route_skips_when_yaml_is_malformed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed zsum.config.yaml is left untouched and used as-is (no transient rewrite)."""
        from logic_loom_api.routes import zsum as zsum_route

        cfg = tmp_path / "zsum.config.yaml"
        broken = "llm:\n  provider: [unclosed\n"
        cfg.write_text(broken, encoding="utf-8")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-route-key")
        settings = Settings(
            zsum_config_path=cfg, cache_path=tmp_path / "cache", zsum_llm_provider="akms"
        )

        # Must not raise; key env still forwarded. The malformed source can't be
        # parsed → the run falls back to the committed (broken) path, never a rewrite.
        config_path, overrides = zsum_route._apply_host_llm_runtime(settings, {})
        assert overrides.get("ANTHROPIC_API_KEY") == "sk-route-key"
        assert config_path == cfg
        assert cfg.read_text(encoding="utf-8") == broken

    @pytest.mark.integration
    def test_concurrent_runs_distinct_choices_get_isolated_transient_files(
        self, tmp_path: Path
    ) -> None:
        """Regression: two runs, same source, DIFFERENT choices ⇒ distinct files.

        The transient file is content-addressed over (source + source text + applied
        overrides), so a global codex-cli run and a request overriding zsum to
        anthropic materialise to DISTINCT paths and each carries its own provider —
        no last-writer-wins contamination even though both share one source config.
        Asserted directly at the _materialize_effective_zsum_config seam (no real
        concurrency needed — the isolation is structural).
        """
        from logic_loom_api.routes import zsum as zsum_route

        cfg = tmp_path / "zsum.config.yaml"
        cfg.write_text("# shared source\nllm:\n  provider: anthropic\n", encoding="utf-8")
        settings = Settings(zsum_config_path=cfg, cache_path=tmp_path / "cache")

        runtime_codex = zsum_adapter.ZsumRuntime(
            provider="codex_exec", config_overrides={"llm.provider": "codex_exec"}
        )
        runtime_anthropic = zsum_adapter.ZsumRuntime(
            provider="anthropic",
            model="claude-sonnet-4-6",
            config_overrides={
                "llm.provider": "anthropic",
                "llm.anthropic.model": "claude-sonnet-4-6",
            },
        )

        path_codex = zsum_route._materialize_effective_zsum_config(settings, cfg, runtime_codex)
        path_anthropic = zsum_route._materialize_effective_zsum_config(
            settings, cfg, runtime_anthropic
        )

        # Distinct content-addressed slots — neither overwrites the other.
        assert path_codex != path_anthropic
        assert path_codex.exists() and path_anthropic.exists()
        # Each file carries ITS OWN provider (no cross-run contamination).
        assert "codex_exec" in path_codex.read_text(encoding="utf-8")
        assert "anthropic" in path_anthropic.read_text(encoding="utf-8")
        assert "codex_exec" not in path_anthropic.read_text(encoding="utf-8")
        assert "claude-sonnet-4-6" not in path_codex.read_text(encoding="utf-8")
        # Committed source untouched by either materialisation.
        assert cfg.read_text(encoding="utf-8") == "# shared source\nllm:\n  provider: anthropic\n"

        # Same fingerprint reuses the same file (bounded — no tmp accumulation).
        path_codex_again = zsum_route._materialize_effective_zsum_config(
            settings, cfg, runtime_codex
        )
        assert path_codex_again == path_codex
        # No leftover *.tmp files from the atomic os.replace writes.
        tmp_leftovers = list(zsum_route._effective_zsum_config_dir(settings).glob("*.tmp"))
        assert tmp_leftovers == []
