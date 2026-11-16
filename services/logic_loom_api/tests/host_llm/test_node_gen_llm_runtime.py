"""Tests for the node-gen runtime: generalised require_binary('claude') + LiteLLM env from host choice.

Covers:
  1 — Global choice codex-cli => require_binary is called for the codex binary, not claude.
  2 — API choice => AKMS_LLM_PROVIDER/AKMS_MODEL set correctly before dispatch.
  3 — No choice => claude fallback unchanged; no generate_nodes_pipeline.py edits (vendored tree clean).

Design: a never-raising host-LLM resolver
``akms_dispatch.resolve_node_gen_runtime(settings)`` mirrors
``adapters/zsum_adapter.resolve_zsum_runtime``. It resolves the effective
node-gen choice via ``host_llm.routing.resolve_choice`` (NOT ``route_for`` — that
eagerly calls ``api_model_for("akms","nodegen")`` which raises
``UnknownProviderError``), maps the host label, looks the model up with a guarded
lookup, and returns ``None`` (claude fallback) on no-choice / unmapped / any
error (degrade gracefully). ``preflight_node_generation`` + ``dispatch_node_generation_to_claude``
take an optional ``settings`` and dispatch the resolved binary / subprocess env.

Tests drive the resolver + the dispatch helpers directly, monkeypatching
``shutil.which`` (PATH) and scrubbing API-key env vars so a CI-exported key can
never false-FAIL the no-choice / degrade cases.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

import logic_loom_api.akms_dispatch as ad
import logic_loom_api.host_llm.discovery as discovery
from logic_loom_api.akms_dispatch import (
    NodeGenRuntime,
    resolve_node_gen_runtime,
)

# Completion key env vars that, if exported by CI, would otherwise flip the
# akms/no-choice resolution. Scrubbed in every test via the autouse fixture.
_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AKMS_LLM_API_BASE",
)


@pytest.fixture(autouse=True)
def _scrub_key_env(monkeypatch):
    """Remove completion-key env vars so resolution is deterministic.

    Without this, a CI-exported ANTHROPIC_API_KEY would make the akms auto-path
    resolve in the no-choice test and false-FAIL it.
    """
    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _settings(**overrides):
    """Minimal duck-typed Settings stub. resolve_choice reads these via getattr."""
    base = {
        "nodegen_llm_provider": None,
        "llm_provider": None,
        "learn_llm_provider": None,
        "learn_llm_custom_clis": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _all_on_path(monkeypatch):
    """Make every binary resolve on PATH (so CLI providers are 'available')."""
    monkeypatch.setattr(discovery.shutil, "which", lambda binary: "/usr/bin/" + binary)


class TestNodeGenLlmRuntime:
    """Tests for generalised require_binary('claude') + LiteLLM env from host choice."""

    @pytest.mark.integration
    def test_codex_cli_choice_requires_codex_binary_not_claude(self, monkeypatch):
        """Verifies: When global choice is codex-cli, route_for resolves it and require_binary is called for codex, not claude.

        Requirement: 1 — 'Global choice codex-cli => require_binary is called for the codex binary, not claude.'
        Passes when: preflight_node_generation or dispatch_node_generation_to_claude calls require_binary("codex") when settings.learn_llm_provider is a codex CLI choice.
        """
        _all_on_path(monkeypatch)
        settings = _settings(nodegen_llm_provider="codex-cli")

        # 1. The resolver picks the codex binary (derived from the argv template),
        #    NOT claude.
        runtime = resolve_node_gen_runtime(settings)
        assert runtime is not None
        assert runtime.binary == "codex"
        assert runtime.provider == "codex-cli"
        assert runtime.env_overrides == {}  # CLI auth via its own session, no env

        # 2. require_binary is invoked for the RESOLVED binary, not "claude".
        #    dispatch_node_generation_to_claude derives its effective binary from
        #    the runtime; we capture which binary the spawn is asked for.
        spawned = {}

        async def fake_exec(*argv, **kwargs):
            spawned["argv"] = list(argv)
            spawned["env"] = kwargs.get("env")
            raise FileNotFoundError("stub: not really spawning")

        monkeypatch.setattr(ad.asyncio, "create_subprocess_exec", fake_exec)

        plan = self._tmp_plan(monkeypatch)

        async def _run():
            gen = ad.dispatch_node_generation_to_claude(
                nlm_id="nb",
                node_selection="all",
                plan_path=plan,
                out_dir=plan.parent / "out",
                cwd=plan.parent,
                settings=settings,
            )
            with pytest.raises(ad.AkmsDispatchError):
                async for _ in gen:
                    pass

        asyncio.run(_run())
        # Regression: codex must dispatch via its OWN
        # template ("codex exec {prompt}"), NOT claude's --print/--output-format json
        # flags (codex rejects --print). Assert the FULL template-aware argv — the old
        # argv[0]-only assertion masked the wrong-flags bug.
        argv = spawned["argv"]
        assert argv[0] == "codex"
        assert argv[1] == "exec"
        assert "--print" not in argv, argv
        assert "--output-format" not in argv, argv
        # The node-gen prompt is substituted into the {prompt} slot as one argv element.
        assert any("Use the node-gen skill" in a for a in argv), argv
        # Non-vacuity: the pre-fix claude-shape builder would have emitted
        # ["codex", "--print", "--output-format", "json", <prompt>] — the asserts on
        # argv[1]=="exec" and "--print" not in argv FAIL under that shape.

    @pytest.mark.integration
    def test_api_choice_sets_akms_llm_provider_env_before_dispatch(self, monkeypatch):
        """Verifies: When global choice is an API provider (e.g., openai), AKMS_LLM_PROVIDER env var is set to the node-gen equivalent (openai) before dispatch.

        Requirement: 2 — 'API choice => AKMS_LLM_PROVIDER/AKMS_MODEL set correctly before dispatch.'
        Passes when: preflight_node_generation or dispatch_node_generation_to_claude sets os.environ['AKMS_LLM_PROVIDER'] = 'openai' (or mapped value) when settings.learn_llm_provider resolves to an API transport.
        """
        # Host completion choice is 'akms'; the concrete API family is derived
        # from the present completion key. Export OPENAI_API_KEY so the family is
        # 'openai' (node-gen's AKMS_LLM_PROVIDER value).
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        settings = _settings(nodegen_llm_provider="akms")

        runtime = resolve_node_gen_runtime(settings)
        assert runtime is not None
        # API path keeps dispatching via the claude agent; the env steers node-gen.
        assert runtime.binary == "claude"
        assert runtime.provider == "akms"
        assert runtime.env_overrides["AKMS_LLM_PROVIDER"] == "openai"
        # AKMS_MODEL is the node-gen openai model from host_llm.model_map.
        assert runtime.env_overrides["AKMS_MODEL"] == "gpt-4o"
        # The present key env is forwarded to the subprocess.
        assert runtime.env_overrides["OPENAI_API_KEY"] == "sk-openai-test"

        # The env reaches the dispatch subprocess via an explicit env= dict, and
        # is NOT written into the parent os.environ (no process-global leak).
        spawned = {}

        async def fake_exec(*argv, **kwargs):
            spawned["argv"] = list(argv)
            spawned["env"] = kwargs.get("env")
            raise FileNotFoundError("stub: not really spawning")

        monkeypatch.setattr(ad.asyncio, "create_subprocess_exec", fake_exec)
        plan = self._tmp_plan(monkeypatch)

        async def _run():
            gen = ad.dispatch_node_generation_to_claude(
                nlm_id="nb",
                node_selection="all",
                plan_path=plan,
                out_dir=plan.parent / "out",
                cwd=plan.parent,
                settings=settings,
            )
            with pytest.raises(ad.AkmsDispatchError):
                async for _ in gen:
                    pass

        asyncio.run(_run())
        # Dispatch binary stays claude on the API path.
        assert spawned["argv"][0] == "claude"
        # AKMS_LLM_PROVIDER / AKMS_MODEL set in the subprocess env before dispatch.
        assert spawned["env"] is not None
        assert spawned["env"]["AKMS_LLM_PROVIDER"] == "openai"
        assert spawned["env"]["AKMS_MODEL"] == "gpt-4o"
        assert spawned["env"]["OPENAI_API_KEY"] == "sk-openai-test"
        # No process-global leak: the parent env is untouched.
        assert "AKMS_LLM_PROVIDER" not in os.environ
        assert "AKMS_MODEL" not in os.environ

    @pytest.mark.integration
    def test_no_choice_uses_claude_fallback_unchanged(self, monkeypatch):
        """Verifies: When no global choice is set, dispatch falls back to require_binary("claude") as today, with no env manipulation.

        Requirement: 3 — 'No choice => claude fallback unchanged; no generate_nodes_pipeline.py edits (vendored tree clean).'
        Passes when: preflight_node_generation or dispatch_node_generation_to_claude calls require_binary("claude") and does not set AKMS_LLM_* env when settings.learn_llm_provider is None or 'auto'.
        """
        # No keys (autouse scrub) and no choice -> resolver returns None.
        settings = _settings()  # all provider fields None
        assert resolve_node_gen_runtime(settings) is None
        # 'auto' is also a no-op.
        assert resolve_node_gen_runtime(_settings(nodegen_llm_provider="auto")) is None

        spawned = {}

        async def fake_exec(*argv, **kwargs):
            spawned["argv"] = list(argv)
            spawned["env"] = kwargs.get("env")
            raise FileNotFoundError("stub: not really spawning")

        monkeypatch.setattr(ad.asyncio, "create_subprocess_exec", fake_exec)
        plan = self._tmp_plan(monkeypatch)

        async def _run():
            gen = ad.dispatch_node_generation_to_claude(
                nlm_id="nb",
                node_selection="all",
                plan_path=plan,
                out_dir=plan.parent / "out",
                cwd=plan.parent,
                settings=settings,
            )
            with pytest.raises(ad.AkmsDispatchError):
                async for _ in gen:
                    pass

        asyncio.run(_run())
        # Fallback binary is claude, exactly as before host-LLM routing existed.
        assert spawned["argv"][0] == "claude"
        # Claude shape preserved (no host CLI template) — bounds the codex-template fix:
        # the no-choice/claude path still uses build_dispatch_argv's --print json shape.
        assert "--print" in spawned["argv"], spawned["argv"]
        assert "--output-format" in spawned["argv"], spawned["argv"]
        # env= is None (inherit parent unchanged) — NO AKMS_LLM_* injected.
        assert spawned["env"] is None

    @pytest.mark.integration
    def test_argv_template_parsing_with_shlex_safe_split(self, monkeypatch):
        """Verifies: When deriving the binary from a CLI argv_template (e.g., codex-cli), shlex.split is used safely (or via _safe_split) and ValueError is caught.

        Requirement: Risk mitigation — 'argv_template->binary extraction must use shlex (never shell) — reuse the Learn _run_cli_template parsing convention.'
        Passes when: A malformed argv_template with unbalanced quotes degrades gracefully to the claude fallback (or a documented 400), never a 500.

        NON-VACUITY: with the malformed CLI's
        FIRST TOKEN forced on PATH, the ONLY reason the label drops out of
        ``available_cli_providers`` is ``_safe_split`` returning ``None`` — so
        this positively pins the load-bearing upstream guard, not merely "it
        happened to not resolve". Empirically confirmed non-vacuous: temporarily
        replacing ``_safe_split`` in ``host_llm.discovery.available_cli_providers``
        with a raw ``shlex.split`` makes this test FAIL with
        ``ValueError: No closing quotation``; restored.
        """
        # Malformed command whose FIRST shlex token ("mytool") is a clean bare
        # word — so the label would be "available" IF the command parsed — but the
        # trailing unbalanced quote makes shlex.split raise. Force every binary
        # onto PATH so PATH presence cannot be the reason the label drops out.
        _all_on_path(monkeypatch)
        malformed = 'mytool --flag "unbalanced'
        # Sanity: a raw shlex.split on this WOULD raise — proving the case is real.
        import shlex

        with pytest.raises(ValueError):
            shlex.split(malformed)

        settings = _settings(
            nodegen_llm_provider="my-broken-tool",
            learn_llm_custom_clis={"my-broken-tool": malformed},
        )

        # (1) POSITIVELY PIN the load-bearing _safe_split guard: the binary IS on
        #     PATH (everything is), so the label can ONLY be excluded from
        #     available_cli_providers because _safe_split returned None. If the
        #     guard regressed to a raw split this assertion would never be
        #     reached — the call would raise ValueError first.
        assert "my-broken-tool" not in discovery.available_cli_providers(settings)

        # (2) END-TO-END: with that malformed choice, resolution degrades to None
        #     (claude fallback) without raising — never a ValueError/500.
        runtime = resolve_node_gen_runtime(settings)
        assert runtime is None  # claude fallback

        # And the dispatch helper, given the same settings, dispatches claude.
        spawned = {}

        async def fake_exec(*argv, **kwargs):
            spawned["argv"] = list(argv)
            raise FileNotFoundError("stub")

        monkeypatch.setattr(ad.asyncio, "create_subprocess_exec", fake_exec)
        plan = self._tmp_plan(monkeypatch)

        async def _run():
            gen = ad.dispatch_node_generation_to_claude(
                nlm_id="nb",
                node_selection="all",
                plan_path=plan,
                out_dir=plan.parent / "out",
                cwd=plan.parent,
                settings=settings,
            )
            with pytest.raises(ad.AkmsDispatchError):
                async for _ in gen:
                    pass

        asyncio.run(_run())
        assert spawned["argv"][0] == "claude"

    @pytest.mark.integration
    def test_in_branch_safe_split_degrades_quietly_when_cli_branch_reached(
        self, monkeypatch, caplog
    ):
        """Defense-in-depth: pin the resolver's OWN in-branch _safe_split guard.

        The PRIMARY guard is ``available_cli_providers._safe_split`` (pinned by
        ``test_argv_template_parsing_with_shlex_safe_split``), which drops a
        malformed label before the resolver's CLI branch runs. This test forces
        the CLI branch to execute anyway — monkeypatching ``available_cli_providers``
        to INCLUDE the malformed label — and pins the in-branch guard.

        Non-vacuity nuance: the resolver's whole-body try/except BELT would also
        catch a raw-split ``ValueError`` and degrade to ``None`` — so asserting
        only ``runtime is None`` would be VACUOUS (true with or without the
        in-branch guard). The load-bearing distinction is the LOG LEVEL: the
        in-branch guard degrades QUIETLY (``logger.debug``, no warning), whereas
        the belt logs a ``logger.warning('... resolution failed ...')``. This
        test therefore asserts BOTH ``runtime is None`` AND that NO belt warning
        fired — which is FALSE if the in-branch guard is removed (the raw split
        would raise, hit the belt, and emit the warning). Empirically confirmed:
        replacing the in-branch ``_safe_split(template)`` with ``shlex.split``
        makes the no-warning assertion FAIL (belt warning appears); restored.
        """
        import logging

        malformed = 'mytool --flag "unbalanced'
        settings = _settings(
            nodegen_llm_provider="forced-broken",
            learn_llm_custom_clis={"forced-broken": malformed},
        )

        # Force the malformed label "available" so resolve_choice resolves it AND
        # the resolver enters the CLI branch (bypassing the upstream
        # available_cli_providers._safe_split guard). resolve_choice's explicit
        # branch lazily imports available_cli_providers from learn_llm_providers;
        # the resolver re-imports it from host_llm.discovery — patch BOTH.
        import logic_loom_api.learn_llm_providers as learn_llm_providers

        monkeypatch.setattr(
            learn_llm_providers, "available_cli_providers", lambda s: ["forced-broken"]
        )
        monkeypatch.setattr(
            discovery, "available_cli_providers", lambda s: ["forced-broken"]
        )

        with caplog.at_level(logging.DEBUG, logger=ad.logger.name):
            runtime = resolve_node_gen_runtime(settings)

        # Degrades to fallback without raising...
        assert runtime is None
        # ...and QUIETLY (in-branch guard handled it) — the belt's warning did
        # NOT fire. This is what distinguishes the in-branch guard from the belt.
        belt_warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "resolution failed" in r.getMessage()
        ]
        assert belt_warnings == [], (
            "in-branch _safe_split should degrade quietly; belt warning fired: "
            f"{[r.getMessage() for r in belt_warnings]}"
        )

    @pytest.mark.integration
    def test_route_for_unmapped_provider_degrades_gracefully(self, monkeypatch):
        """Verifies: When route_for resolves a custom label to an unmapped provider, the dispatch degrades gracefully to claude fallback or a documented 400, never a 500.

        Requirement: Risk mitigation — 'route_for's API bucket raises UnknownProviderError for a resolvable-but-unmapped custom-label provider — guard it to the claude fallback/400, not a 500.'
        Passes when: An unmapped-but-resolvable provider is handled without raising (catches UnknownProviderError).
        """
        # Two facets of the UnknownProviderError guard:

        # (a) The guarded model lookup degrades to None on UnknownProviderError —
        #     proving api_model_for raising never escapes resolution.
        assert ad._node_gen_model_for("totally-unmapped-family") is None

        # (b) A resolvable custom label is a CLI provider (in
        #     available_cli_providers) so it dispatches its OWN binary and never
        #     reaches the API/route_for bucket — it cannot raise
        #     UnknownProviderError. Confirm it resolves cleanly to a CLI runtime.
        _all_on_path(monkeypatch)
        settings = _settings(
            nodegen_llm_provider="weird-label",
            learn_llm_custom_clis={"weird-label": "weird-tool --do-thing"},
        )
        runtime = resolve_node_gen_runtime(settings)
        assert runtime is not None
        assert runtime.binary == "weird-tool"
        assert runtime.env_overrides == {}

        # (c) Belt: even if model_map.api_model_for were called and raised, the
        #     whole resolver still degrades to None rather than 500. Force the
        #     akms API path (key present) but make api_model_for blow up.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        from logic_loom_api.host_llm import model_map

        def boom(*a, **k):
            raise model_map.UnknownProviderError("boom")

        monkeypatch.setattr(model_map, "api_model_for", boom)
        akms_settings = _settings(nodegen_llm_provider="akms")
        # Must NOT raise — model degrades to None, provider env still set.
        runtime2 = resolve_node_gen_runtime(akms_settings)
        assert runtime2 is not None
        assert runtime2.provider == "akms"
        assert runtime2.model is None  # guarded lookup degraded
        assert runtime2.env_overrides["AKMS_LLM_PROVIDER"] == "anthropic"
        assert "AKMS_MODEL" not in runtime2.env_overrides

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _tmp_plan(self, monkeypatch):
        """Create a tmp plan json file so dispatch's plan_path.exists() passes."""
        import tempfile
        from pathlib import Path

        d = Path(tempfile.mkdtemp())
        plan = d / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        return plan
