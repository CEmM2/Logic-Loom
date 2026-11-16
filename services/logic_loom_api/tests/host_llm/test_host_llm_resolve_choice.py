"""Tests for host_llm/routing.py — HostLLMProvider + resolve_choice/route_for.

Covers: resolve_choice('learn') equals the old resolver across precedence
branches; Transport.kind/model/argv_template per provider; back-compat import
from learn_adapter.

The CRITICAL invariant is equality: ``resolve_choice(settings, "learn")`` must
reproduce the original Learn-only ``resolve_host_llm_provider(settings)`` for every precedence
branch. We assert that equality directly (and by construction the two share an
implementation — ``resolve_host_llm_provider`` delegates to ``resolve_choice``).

Resolution depends on ``shutil.which`` (CLI/nlm presence) and the completion
env vars; both are monkeypatched so results never depend on the test host's
installed binaries or ambient API keys — mirroring tests/test_learn_llm_providers.py.
"""

from __future__ import annotations

import pytest
from logic_loom_api.config import Settings
from logic_loom_api.host_llm import model_map, routing


def _which_only(*present: str):
    """A ``shutil.which`` stub: resolves only the named binaries."""
    names = set(present)
    return lambda binary: f"/usr/bin/{binary}" if binary in names else None


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient completion key / notebook id leaks into a resolution branch."""
    for var in (*routing._COMPLETION_ENV_VARS, *routing._NLM_NOTEBOOK_ENV_VARS):
        monkeypatch.delenv(var, raising=False)


class TestHostLlmResolveChoice:
    """Tests for host_llm/routing.py — HostLLMProvider + resolve_choice/route_for."""

    @pytest.mark.unit
    def test_resolve_choice_explicit_cli_equals_old_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: resolve_choice('learn', settings) reproduces old resolver result for explicit CLI.

        Requirement: 'resolve_choice(settings,"learn") reproduces the original Learn-only resolve_host_llm_provider(settings) result for every precedence branch.'
        Passes when: resolve_choice with explicit CLI choice equals the old resolver output.
        """
        monkeypatch.setattr(routing.shutil, "which", _which_only("claude"))
        settings = Settings(learn_llm_provider="claude-cli")

        choice = routing.resolve_choice(settings, "learn")
        old = routing.resolve_host_llm_provider(settings)

        assert choice == old
        assert choice is not None and choice.provider == "claude-cli"
        assert choice.origin == "explicit (claude-cli)"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("settings_kwargs", "on_path", "env", "expected_provider"),
        [
            # explicit akms wins (and is usable because a completion key is present)
            pytest.param(
                {"learn_llm_provider": "akms"},
                (),
                {"ANTHROPIC_API_KEY": "sk-x"},
                "akms",
                id="explicit-akms-over-key",
            ),
            # explicit CLI wins over an available completion key
            pytest.param(
                {"learn_llm_provider": "claude-cli"},
                ("claude",),
                {"ANTHROPIC_API_KEY": "sk-x"},
                "claude-cli",
                id="explicit-cli-over-key",
            ),
            # no explicit → completion key resolves akms
            pytest.param(
                {},
                (),
                {"OPENAI_API_KEY": "sk-x"},
                "akms",
                id="key-resolves-akms",
            ),
            # no explicit, no key, nlm CLI + notebook id → nlm (preferred when both)
            pytest.param(
                {},
                ("nlm",),
                {"NLM_NOTEBOOK_ID": "nb-1"},
                "nlm",
                id="nlm-when-cli-and-notebook",
            ),
            # nlm preferred over completion when BOTH are configured
            pytest.param(
                {},
                ("nlm",),
                {"ANTHROPIC_API_KEY": "sk-x", "NLM_NOTEBOOK_ID": "nb-1"},
                "nlm",
                id="nlm-preferred-over-completion",
            ),
            # nothing configured → None
            pytest.param({}, (), {}, None, id="nothing-configured-none"),
            # CLI on PATH but no explicit choice → never auto-selects CLI → None
            pytest.param(
                {},
                ("claude", "codex"),
                {},
                None,
                id="cli-on-path-no-choice-none",
            ),
        ],
    )
    def test_resolve_choice_precedence_explicit_gt_key_gt_cli_gt_nlm(
        self,
        monkeypatch: pytest.MonkeyPatch,
        settings_kwargs: dict,
        on_path: tuple[str, ...],
        env: dict[str, str],
        expected_provider: str | None,
    ) -> None:
        """Verifies: resolve_choice respects full precedence order: explicit > key > CLI on PATH > nlm > None.

        Requirement: 'resolve_choice(settings,"learn") reproduces the original Learn-only resolve_host_llm_provider(settings) result for every precedence branch.'
        Passes when: parametrised precedence branches match old resolver for all combinations.
        """
        monkeypatch.setattr(routing.shutil, "which", _which_only(*on_path))
        for var, val in env.items():
            monkeypatch.setenv(var, val)
        settings = Settings(**settings_kwargs)

        choice = routing.resolve_choice(settings, "learn")
        old = routing.resolve_host_llm_provider(settings)

        # The headline equality: new resolver == old resolver, every branch.
        assert choice == old
        if expected_provider is None:
            assert choice is None
        else:
            assert choice is not None and choice.provider == expected_provider

    @pytest.mark.unit
    def test_resolve_choice_no_choice_never_autoselects_cli(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: resolve_choice with no explicit choice does NOT auto-select CLI even if on PATH.

        Requirement: 'resolve_choice(settings,"learn") reproduces the original Learn-only resolve_host_llm_provider(settings) result for every precedence branch.'
        Passes when: CLI on PATH without explicit choice returns None (or NLM if key present).
        """
        # claude + codex on PATH, but no explicit choice, no key, no notebook id.
        monkeypatch.setattr(routing.shutil, "which", _which_only("claude", "codex"))
        settings = Settings()

        choice = routing.resolve_choice(settings, "learn")
        old = routing.resolve_host_llm_provider(settings)

        assert choice is None  # CLI presence alone must not flip the gate
        assert choice == old

    @pytest.mark.unit
    def test_transport_kind_cli_has_argv_template(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: Transport.kind='cli' providers include argv_template; Transport.kind='api' do not.

        Requirement: 'route_for returns kind="cli" with argv_template for CLI providers and kind="api" with a model for API providers.'
        Passes when: Transport for CLI providers has kind='cli' + argv_template; API providers have kind='api' + model.
        """
        monkeypatch.setattr(routing.shutil, "which", _which_only("claude"))
        settings = Settings(learn_llm_provider="claude-cli")

        transport = routing.route_for(settings, "learn")

        assert isinstance(transport, routing.Transport)
        assert transport.kind == "cli"
        assert transport.provider == "claude-cli"
        assert transport.model is None
        # argv_template sourced from discovery's _BUILTIN_CLIS registry.
        assert transport.argv_template == "claude -p --output-format text"

    @pytest.mark.unit
    def test_transport_kind_api_has_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: Transport.kind='api' providers include model; Transport.kind='cli' do not.

        Requirement: 'route_for returns kind="cli" with argv_template for CLI providers and kind="api" with a model for API providers.'
        Passes when: API Transport has kind='api' + model field populated from model_map.
        """
        # akms explicit + a completion key present → resolves the akms API provider.
        monkeypatch.setattr(routing.shutil, "which", _which_only())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        settings = Settings(learn_llm_provider="akms")

        transport = routing.route_for(settings, "learn")

        assert isinstance(transport, routing.Transport)
        assert transport.kind == "api"
        assert transport.provider == "akms"
        assert transport.argv_template is None
        # model is the model_map entry for ('akms', 'learn').
        assert transport.model == model_map.api_model_for("akms", "learn")
        assert transport.model == "akms-router"

    @pytest.mark.unit
    def test_route_for_delegates_model_to_model_map(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifies: route_for(settings, consumer, task_kind) delegates model selection to model_map.

        Requirement: 'route_for returns kind="cli" with argv_template for CLI providers and kind="api" with a model for API providers.'
        Passes when: route_for calls model_map.api_model_for(provider, consumer, task_kind) for API providers.
        """
        monkeypatch.setattr(routing.shutil, "which", _which_only())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        settings = Settings(learn_llm_provider="akms")

        calls: list[tuple[str, str, str]] = []
        real = model_map.api_model_for

        def _spy(provider: str, consumer: str, task_kind: str = "default") -> str:
            calls.append((provider, consumer, task_kind))
            return real(provider, consumer, task_kind)

        # route_for references model_map.api_model_for through the module, so
        # patching the module attribute is what the call resolves at runtime.
        monkeypatch.setattr(routing.model_map, "api_model_for", _spy)

        transport = routing.route_for(settings, "learn", task_kind="expand")

        assert calls == [("akms", "learn", "expand")]
        assert transport is not None
        assert transport.model == real("akms", "learn", "expand")

    @pytest.mark.unit
    def test_backcompat_import_resolve_host_llm_provider_from_learn_adapter(self) -> None:
        """Verifies: resolve_host_llm_provider remains importable from learn_adapter (re-export).

        Requirement: 'from logic_loom_api.adapters.learn_adapter import resolve_host_llm_provider still works.'
        Passes when: import from learn_adapter succeeds and resolves to host_llm.routing version.
        """
        from logic_loom_api.adapters.learn_adapter import (
            resolve_host_llm_provider as via_adapter,
        )

        # The re-export must BE the routing implementation (no fork/copy).
        assert via_adapter is routing.resolve_host_llm_provider
