"""Tests for the host LLM provider bridge into akms_learn.

The bridge threads a host-configured LLM provider into akms_learn's provider
seam so the ``llm_expanded`` / ``adaptive_path`` modes become selectable and
compile end-to-end when (and only when) the desktop host has a provider
configured. Coverage:

* **Resolution** — :func:`resolve_host_llm_provider` maps host config (completion
  env key / Settings ▸ LLM zsum config / nlm CLI + notebook id) onto an
  akms_learn registry name, prefers grounded ``nlm`` over ``akms`` completion,
  and degrades to ``None`` (never raises) when nothing is configured or config is
  broken.
* **Availability flip** — ``get_generation_mode_status`` reports an llm-gated mode
  ``available`` when a host provider resolves, leaves it gated otherwise, and
  never touches non-llm modes.
* **Request threading** — :func:`learn_adapter.compile` sets
  ``llm_enable``/``llm_provider``/``sources`` on the ``LearningRequest`` only for
  LLM-expansion modes with a resolved provider; non-llm modes and the
  no-provider path stay on akms_learn's default (stub) path.
* **Route gate** — ``POST /api/learn/compile`` rejects an llm mode with a clean
  412 ``capability_unavailable`` when no provider is configured.

Patterns mirror tests/test_learn_backend_suite.py (tmp-path client, fixture graph slice,
compile-spy via monkeypatching ``akms_learn.compile_learning_source``).
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from logic_loom_api.adapters import learn_adapter
from logic_loom_api.config import Settings

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None
pytestmark = pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")

_ALL_PROVIDER_ENV = (*learn_adapter._COMPLETION_ENV_VARS, *learn_adapter._NLM_NOTEBOOK_ENV_VARS)


@pytest.fixture()
def no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Baseline: no provider configured anywhere (env cleared, nlm CLI absent)."""
    for var in _ALL_PROVIDER_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(learn_adapter.shutil, "which", lambda _name: None)


def _spy_compile(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace akms_learn.compile_learning_source with a request-capturing spy.

    ``learn_adapter.compile`` does a function-local ``from akms_learn import
    compile_learning_source``, so patching the module attribute is what that
    import resolves at call time. Returns a captured-state dict; the spy returns a
    sentinel (compile only wraps it in a LearnCompileOutcome, which we ignore).
    """
    import akms_learn

    captured: dict[str, Any] = {}

    def _spy(request: Any, **kwargs: Any) -> Any:
        captured["request"] = request
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(akms_learn, "compile_learning_source", _spy)
    return captured


def _compile(generation_option: str, *, settings: Settings | None = None, **extra: Any) -> Any:
    import akms_learn

    return learn_adapter.compile(
        topic="j2 return mapping",
        goal="Understand the j2 return-mapping algorithm",
        generation_option=generation_option,
        graph_slice=akms_learn.fixture_graph(),
        settings=settings,
        **extra,
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestResolveHostProvider:
    @pytest.mark.unit
    def test_none_when_unconfigured(self, no_provider: None) -> None:
        assert learn_adapter.resolve_host_llm_provider(None) is None
        assert learn_adapter.host_llm_modes_available(None) is False

    @pytest.mark.unit
    def test_completion_from_env_key(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        provider = learn_adapter.resolve_host_llm_provider(None)
        assert provider is not None
        assert provider.provider == learn_adapter._COMPLETION_PROVIDER_NAME
        assert provider.sources is None
        assert "OPENAI_API_KEY" in provider.origin

    @pytest.mark.unit
    def test_nlm_from_cli_and_notebook(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NLM_NOTEBOOK_ID", "nb-123")
        monkeypatch.setattr(
            learn_adapter.shutil, "which", lambda name: "/usr/bin/nlm" if name == "nlm" else None
        )
        provider = learn_adapter.resolve_host_llm_provider(None)
        assert provider is not None
        assert provider.provider == learn_adapter._NLM_PROVIDER_NAME
        assert provider.sources == {"notebook_id": "nb-123"}

    @pytest.mark.unit
    def test_nlm_preferred_over_completion(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AKMS_LEARN_NLM_NOTEBOOK_ID", "nb-9")
        monkeypatch.setattr(
            learn_adapter.shutil, "which", lambda name: "/usr/bin/nlm" if name == "nlm" else None
        )
        provider = learn_adapter.resolve_host_llm_provider(None)
        assert provider is not None
        assert provider.provider == learn_adapter._NLM_PROVIDER_NAME

    @pytest.mark.unit
    def test_nlm_unconfigured_without_notebook_id(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # CLI present but no notebook id → not configured.
        monkeypatch.setattr(
            learn_adapter.shutil, "which", lambda name: "/usr/bin/nlm" if name == "nlm" else None
        )
        assert learn_adapter._resolve_nlm_provider(None) is None

    @pytest.mark.unit
    def test_degrades_gracefully_on_broken_zsum_config(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "zsum.config.yaml"
        cfg.write_text("llm: {}\n", encoding="utf-8")
        settings = Settings(zsum_config_path=cfg)

        import zsum.config

        def _boom(_path: Any) -> Any:
            raise RuntimeError("corrupt config")

        monkeypatch.setattr(zsum.config, "load_config", _boom)
        # Must not raise, and resolves to None (no completion, no nlm).
        assert learn_adapter.resolve_host_llm_provider(settings) is None


class TestZsumCompletionConfig:
    """The Settings ▸ LLM (zsum.config.yaml) path of completion resolution."""

    @staticmethod
    def _fake_zsum(
        monkeypatch: pytest.MonkeyPatch, *, provider: str, api_key_env: str | None
    ) -> None:
        import zsum.config

        class _ProviderCfg:
            pass

        provider_cfg = _ProviderCfg()
        if api_key_env is not None:
            provider_cfg.api_key_env = api_key_env  # type: ignore[attr-defined]

        class _LLM:
            def __init__(self) -> None:
                self.provider = provider

            def config_for_provider(self, _p: str) -> Any:
                return provider_cfg

        class _Cfg:
            llm = _LLM()

        monkeypatch.setattr(zsum.config, "load_config", lambda _path: _Cfg())

    @pytest.mark.unit
    def test_resolves_via_zsum_when_api_key_env_present(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "zsum.config.yaml"
        cfg.write_text("llm: {}\n", encoding="utf-8")
        self._fake_zsum(monkeypatch, provider="anthropic", api_key_env="ANTHROPIC_API_KEY")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        provider = learn_adapter.resolve_host_llm_provider(Settings(zsum_config_path=cfg))
        assert provider is not None
        assert provider.provider == learn_adapter._COMPLETION_PROVIDER_NAME
        assert "zsum llm config" in provider.origin

    @pytest.mark.unit
    def test_nonstandard_api_key_env_degrades_to_none(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # api_key_env names a non-standard var the akms completion adapter cannot
        # route — even if set, completion stays unconfigured (degrade gracefully).
        cfg = tmp_path / "zsum.config.yaml"
        cfg.write_text("llm: {}\n", encoding="utf-8")
        self._fake_zsum(monkeypatch, provider="openai_compat", api_key_env="CUSTOM_LLM_KEY")
        monkeypatch.setenv("CUSTOM_LLM_KEY", "secret")

        assert learn_adapter.resolve_host_llm_provider(Settings(zsum_config_path=cfg)) is None


# ---------------------------------------------------------------------------
# Availability flip
# ---------------------------------------------------------------------------


class TestAvailabilityFlip:
    @pytest.mark.unit
    def test_llm_gated_modes_include_llm_expanded(self) -> None:
        assert "llm_expanded" in learn_adapter._llm_gated_modes()

    @pytest.mark.unit
    def test_llm_mode_unavailable_when_unconfigured(self, no_provider: None) -> None:
        status = learn_adapter.get_generation_mode_status("llm_expanded", Settings())
        assert status != "available"

    @pytest.mark.unit
    def test_llm_mode_flips_available_when_configured(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        status = learn_adapter.get_generation_mode_status("llm_expanded", Settings())
        assert status == "available"

    @pytest.mark.unit
    def test_no_settings_means_no_flip(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even with a key in env, passing settings=None leaves the env-only gate
        # to akms_learn (no host-aware override applied here).
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        baseline = learn_adapter.get_generation_mode_status("llm_expanded", None)
        flipped = learn_adapter.get_generation_mode_status("llm_expanded", Settings())
        # The settings-aware call is available; the None call only reflects the
        # akms_learn env gate (which here also sees the key) — assert the flip is
        # at least never weaker than the un-flipped result.
        assert flipped == "available"
        assert baseline in {"available", "unavailable", "planned", None}

    @pytest.mark.unit
    def test_non_llm_mode_unaffected_by_provider(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = learn_adapter.get_generation_mode_status("deterministic_outline", Settings())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        after = learn_adapter.get_generation_mode_status("deterministic_outline", Settings())
        assert before == after  # provider config never changes a non-llm mode's status


# ---------------------------------------------------------------------------
# Request threading
# ---------------------------------------------------------------------------


class TestRequestThreading:
    @pytest.mark.unit
    def test_llm_expanded_threads_completion(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        captured = _spy_compile(monkeypatch)
        _compile("llm_expanded", policy="source_locked")
        request = captured["request"]
        assert request.llm_enable is True
        assert request.llm_provider == learn_adapter._COMPLETION_PROVIDER_NAME
        assert request.llm_policy == "source_locked"

    @pytest.mark.unit
    def test_llm_expanded_threads_nlm_sources(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NLM_NOTEBOOK_ID", "nb-77")
        monkeypatch.setattr(
            learn_adapter.shutil, "which", lambda name: "/usr/bin/nlm" if name == "nlm" else None
        )
        captured = _spy_compile(monkeypatch)
        _compile("llm_expanded")
        request = captured["request"]
        assert request.llm_enable is True
        assert request.llm_provider == learn_adapter._NLM_PROVIDER_NAME
        assert request.sources == {"notebook_id": "nb-77"}

    @pytest.mark.unit
    def test_llm_expanded_without_provider_defers_to_stub(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _spy_compile(monkeypatch)
        _compile("llm_expanded")  # no provider configured
        request = captured["request"]
        # Unset → akms_learn default False → silent no_provider_stub path, no raise.
        assert request.llm_enable is False

    @pytest.mark.unit
    def test_non_llm_mode_not_threaded(
        self, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # provider configured…
        captured = _spy_compile(monkeypatch)
        _compile("deterministic_outline")  # …but the mode is not an LLM-expansion mode
        request = captured["request"]
        assert request.llm_enable is False


class TestUpstreamConstantParity:
    """Drift-guard: the local registry-name literals must equal upstream's public
    constants (the literals are kept local for clean-import-when-akms_learn-absent;
    this test catches any upstream rename instead of letting resolution silently
    break)."""

    @pytest.mark.unit
    def test_provider_names_match_upstream(self) -> None:
        from akms_learn.llm.providers.akms_completion import AKMS_PROVIDER_NAME
        from akms_learn.llm.providers.nlm_cli import NLM_PROVIDER_NAME

        assert learn_adapter._COMPLETION_PROVIDER_NAME == AKMS_PROVIDER_NAME
        assert learn_adapter._NLM_PROVIDER_NAME == NLM_PROVIDER_NAME


# ---------------------------------------------------------------------------
# Route gate (integration)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    cfg_path = tmp_path / "config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg_path),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        },
    ):
        from logic_loom_api.deps import reload_settings

        reload_settings()
        from logic_loom_api.main import api

        with TestClient(api) as c:
            yield c
        reload_settings()


class TestRouteGate:
    @pytest.mark.unit
    def test_llm_mode_rejected_412_when_unconfigured(self, client: Any, no_provider: None) -> None:
        from akms_learn import fixture_graph

        gs = fixture_graph()
        resp = client.post(
            "/api/learn/compile",
            json={
                "generation_option": "llm_expanded",
                "topic": "j2 return mapping",
                "goal": "Understand the j2 return-mapping algorithm",
                "graph_slice": {
                    "nodes": list(gs.nodes),
                    "edges": list(gs.edges),
                    "metadata": dict(gs.metadata),
                },
            },
        )
        assert resp.status_code == 412
        assert resp.json()["error"]["code"] == "capability_unavailable"

    @pytest.mark.unit
    def test_profiles_reflect_host_provider(
        self, client: Any, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # GET /profiles drives the Learn-tab mode selector, so it must apply the
        # host-aware flip too — unconfigured llm mode disabled, configured enabled,
        # non-llm modes unchanged either way.
        unconfigured = {
            p["id"]: p["status"] for p in client.get("/api/learn/profiles").json()["profiles"]
        }
        assert unconfigured.get("llm_expanded") == "unavailable"

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        configured = {
            p["id"]: p["status"] for p in client.get("/api/learn/profiles").json()["profiles"]
        }
        assert configured.get("llm_expanded") == "available"
        # A non-llm mode is unaffected by provider configuration.
        assert (
            unconfigured.get("deterministic_outline")
            == configured.get("deterministic_outline")
            == "available"
        )
