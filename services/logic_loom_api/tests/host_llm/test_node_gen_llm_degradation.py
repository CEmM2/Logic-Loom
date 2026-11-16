"""Tests for node-gen degradation — missing binary → 400, fallback preserved.

Covers:
  1. Chosen codex-cli with codex absent → HTTP 400, body names codex + the provider;
     sidecar stays up.
  2. Unset choice with claude present → dispatch succeeds via claude.

Degrade-gracefully cases (the CLAUDE.md degrade-gracefully invariant):
  3. Malformed custom-CLI command (shlex.split ValueError) must degrade via
     _safe_split (returns None), never 500 on the node-gen request path.
  4. Resolvable-but-unmapped custom-label provider (route_for UnknownProviderError)
     must degrade to claude fallback / 400, never 500 on the node-gen request path.

Design: a small fail-fast gate ``akms_dispatch.require_node_gen_binary(settings)``
reads the RAW effective node-gen choice (BEFORE ``available_cli_providers``'s PATH
filter — the only place a chosen-but-missing CLI is still distinguishable from a
silent degrade) and:
  - explicit built-in/valid-custom CLI whose binary is MISSING → AkmsDispatchError
    naming the binary + provider → routes/akms_dispatch.create_run maps it to 400 (case 1).
  - malformed custom / unmapped / no-choice / akms-API → returns the effective binary
    (resolver result, "claude" fallback) WITHOUT raising (cases 2, 3 and 4).

The 400-route case is driven through the FastAPI TestClient against a tmp cache_path
(CLAUDE.md: tests hitting /api runs need a tmp cache_path). The degrade cases drive
the gate helper directly (no subprocess needed), mirroring the
test_argv_template_parsing_with_shlex_safe_split non-vacuity pattern in
test_node_gen_llm_runtime.py: force the
first token on PATH so _safe_split → None is the ONLY cause of the degrade.

API-key env vars are scrubbed in every test so a CI-exported key cannot flip the
no-choice / akms resolution and false-FAIL.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import logic_loom_api.akms_dispatch as ad
import logic_loom_api.host_llm.discovery as discovery
from logic_loom_api.akms_dispatch import AkmsDispatchError, require_node_gen_binary

# Completion key env vars that, if exported by CI, would flip the akms/no-choice
# resolution. Scrubbed in every test via the autouse fixture.
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
    resolve and could flip the no-choice / degrade assertions.
    """
    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _settings(**overrides):
    """Minimal duck-typed Settings stub. The gate + resolver read these via getattr.

    Deliberately a SimpleNamespace WITHOUT an effective_llm_provider method so the
    gate exercises its stub-Settings getattr fallback (the real Settings path is
    covered end-to-end by the TestClient route tests below).
    """
    base = {
        "nodegen_llm_provider": None,
        "llm_provider": None,
        "learn_llm_provider": None,
        "learn_llm_custom_clis": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _which_missing(*missing):
    """A shutil.which replacement: everything is on PATH except *missing*."""
    miss = set(missing)

    def which(binary):
        if binary in miss:
            return None
        return "/usr/bin/" + binary

    return which


def _all_on_path(monkeypatch):
    """Make every binary resolve on PATH (so CLI providers are 'available')."""
    monkeypatch.setattr(discovery.shutil, "which", lambda binary: "/usr/bin/" + binary)


# --------------------------------------------------------------------------- #
# Route harness (mirrors tests/akms/test_node_generation_routes.py): minimal app, tmp cache_path.
# --------------------------------------------------------------------------- #
def _build_test_app(ws: Path):
    from fastapi import FastAPI
    from logic_loom_api.routes import akms_dispatch as akms_routes
    from logic_loom_api.runs.registry import init_registry

    app = FastAPI()
    app.include_router(akms_routes.router, prefix="/api/akms/node-generation")
    cache = ws.parent / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    init_registry(cache, zsum_repo=None)
    return app


@pytest.fixture()
def akms_app(tmp_path, monkeypatch):
    """TestClient with an isolated workspace + seeded batch + tmp cache_path."""
    from logic_loom_api.batches import BatchStore, EditableBatch

    ws = tmp_path / "akms-workspace"
    ws.mkdir()
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(ws))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    # Point at a non-existent config so only env + defaults feed Settings.load().
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))

    store = BatchStore()
    store.add(EditableBatch(id="batch-1", title="Test Batch"))
    store.save(ws)

    app = _build_test_app(ws)
    return app, ws, monkeypatch


class TestNodeGenLlmDegradation:
    """Tests for node-gen degradation (missing binary → 400, fallback preserved).

    Covers: missing binary 400, fallback success, plus the degrade-gracefully
    cases: malformed custom CLI, unmapped provider.
    """

    @pytest.mark.integration
    def test_missing_binary_returns_400_with_provider_name(self, akms_app):
        """Verifies: Chosen provider with missing binary → HTTP 400 status,
        body names the binary and provider, sidecar stays up.

        Requirement: 'Chosen codex-cli with codex absent → HTTP 400, body names codex + the provider;
        sidecar stays up.'

        Drives POST /api/akms/node-generation/runs through the FastAPI TestClient
        with nodegen_llm_provider=codex-cli (env) and `codex` forced OFF PATH. The
        route's require_node_gen_binary gate raises AkmsDispatchError → 400 BEFORE a
        run is minted; the 400 body names BOTH 'codex' AND 'codex-cli'.

        Non-vacuity: claude IS on PATH (only codex is missing), so the 400 can ONLY
        come from the chosen-codex-cli-missing gate, not an incidental failure. After
        the 400 the same client serves a 404 (sidecar still up).
        """
        app, _ws, mp = akms_app
        # Choose codex-cli explicitly via env (Settings.load reads LOGIC_LOOM_*).
        mp.setenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", "codex-cli")
        # codex absent, everything else (incl. claude) present — so the ONLY reason
        # a 400 can fire is the chosen codex-cli binary being missing. Patch BOTH
        # the gate's shutil (akms_dispatch) AND discovery's (resolver path) so the
        # result is deterministic even on a host that actually has codex installed.
        mp.setattr(ad.shutil, "which", _which_missing("codex"))
        mp.setattr(discovery.shutil, "which", _which_missing("codex"))

        with TestClient(app) as client:
            resp = client.post(
                "/api/akms/node-generation/runs",
                json={"batch_id": "batch-1", "dry_run": False},
            )
            assert resp.status_code == 400, resp.text
            detail = str(resp.json().get("detail", ""))
            # Body names BOTH the missing binary AND the chosen provider label.
            assert "codex" in detail, detail
            assert "codex-cli" in detail, detail

            # Sidecar stays up: the same client still serves other requests.
            still_up = client.get("/api/akms/node-generation/runs/nope")
            assert still_up.status_code == 404, still_up.text

    @pytest.mark.integration
    def test_fallback_to_claude_on_unset_choice(self, akms_app):
        """Verifies: When no node-gen provider choice is set, dispatch succeeds via
        the claude fallback (the gate does not raise).

        Requirement: 'Unset choice with claude present → dispatch succeeds via claude.'

        Drives POST /runs with NO provider choice (keys scrubbed) and every binary
        on PATH. The gate returns 'claude' without raising, so start_workflow runs:
        a dry_run returns a 200 preview (mirroring the node-generation routes dry-run test) — proving the
        gate did not block the claude fallback path.
        """
        app, _ws, mp = akms_app
        # No LOGIC_LOOM_NODEGEN_LLM_PROVIDER / llm_provider set; keys scrubbed by the
        # autouse fixture → resolver returns None → gate returns 'claude'.
        mp.delenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", raising=False)
        mp.delenv("LOGIC_LOOM_LLM_PROVIDER", raising=False)
        mp.delenv("LOGIC_LOOM_LEARN_LLM_PROVIDER", raising=False)
        # claude (and everything) on PATH.
        mp.setattr(ad.shutil, "which", lambda binary: "/usr/bin/" + binary)

        with TestClient(app) as client:
            # Sanity: the gate itself returns claude and does NOT raise.
            from logic_loom_api.config import Settings

            assert require_node_gen_binary(Settings.load()) == "claude"

            # dry_run goes through RunService.start_workflow past the gate → 200.
            resp = client.post(
                "/api/akms/node-generation/runs",
                json={"batch_id": "batch-1", "dry_run": True},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "preview"

    @pytest.mark.regression
    def test_malformed_custom_cli_degrades_not_raises(self, monkeypatch):
        """Verifies: A malformed custom-CLI command (unbalanced quote) reaches
        _safe_split, degrades to None (no parseable binary), and the gate returns
        the claude fallback WITHOUT raising — never a 500 from shlex.ValueError.

        Requirement: degrade gracefully.

        Non-vacuity (mirrors test_argv_template_parsing_with_shlex_safe_split):
        the malformed command's FIRST token ('mytool') is a clean bare word forced
        ON PATH, so the ONLY thing that makes the gate degrade instead of 400 is
        _safe_split returning None on the unbalanced quote. A sanity assert proves a
        RAW shlex.split on the command WOULD raise — so the case is real, and the
        gate's clean return positively pins the load-bearing _safe_split guard.
        """
        import shlex

        _all_on_path(monkeypatch)
        # Also make the gate's own shutil.which see everything on PATH, so PATH
        # presence can never be the reason for the degrade.
        monkeypatch.setattr(ad.shutil, "which", lambda binary: "/usr/bin/" + binary)

        malformed = 'mytool --flag "unbalanced'
        # Sanity: a raw shlex.split WOULD raise — proving the malformed case is real
        # and that a regression to raw-split would 500 here.
        with pytest.raises(ValueError):
            shlex.split(malformed)

        settings = _settings(
            nodegen_llm_provider="my-broken-tool",
            learn_llm_custom_clis={"my-broken-tool": malformed},
        )

        # The gate degrades to the claude fallback WITHOUT raising AkmsDispatchError
        # (would be a 400) or ValueError (would be a 500). Since _safe_split returns
        # None, no binary can be named — so degrade, not 400.
        binary = require_node_gen_binary(settings)
        assert binary == "claude"

    @pytest.mark.regression
    def test_unmapped_provider_degrades_to_fallback(self, monkeypatch):
        """Verifies: A stray/unmapped provider label (not a built-in CLI, not a
        configured custom label, not akms/nlm) degrades to the claude fallback
        WITHOUT raising — never an AkmsDispatchError(400) or UnknownProviderError(500)
        on the node-gen request path.

        Requirement: degrade gracefully.

        Two facets:
        (a) An UNKNOWN label with no custom-CLI entry → the gate hits its 'unmapped'
            branch and returns 'claude' (cannot/should-not 400 a label that names no
            installable binary). Non-vacuity: every binary is on PATH, so a missing
            binary is NOT the reason — the label simply maps to nothing.
        (b) A RESOLVABLE custom label (binary on PATH) is a CLI provider, so the gate
            returns ITS binary (proving the resolvable path is honoured, not blanket-
            degraded). This bounds the degrade: only TRULY unmapped/missing labels
            degrade; a usable custom CLI dispatches its own binary.
        """
        _all_on_path(monkeypatch)
        monkeypatch.setattr(ad.shutil, "which", lambda binary: "/usr/bin/" + binary)

        # (a) Stray label, no custom-CLI mapping → unmapped branch → claude.
        unmapped = _settings(nodegen_llm_provider="totally-made-up-provider")
        assert require_node_gen_binary(unmapped) == "claude"

        # (b) A resolvable custom label dispatches its own binary (not degraded).
        resolvable = _settings(
            nodegen_llm_provider="weird-label",
            learn_llm_custom_clis={"weird-label": "weird-tool --do-thing"},
        )
        assert require_node_gen_binary(resolvable) == "weird-tool"

    # ------------------------------------------------------------------ #
    # Extra focused gate assertions (helper-direct) — strengthen case 1.
    # ------------------------------------------------------------------ #
    @pytest.mark.integration
    def test_gate_raises_naming_binary_and_provider_for_missing_codex(
        self, monkeypatch
    ):
        """Direct gate test for case 1: codex-cli chosen, codex absent → AkmsDispatchError
        whose message names BOTH the binary ('codex') and the provider ('codex-cli').

        This pins the raise/message contract independently of the route wiring (the
        route test pins the AkmsDispatchError → 400 mapping). Non-vacuity: claude is
        forced ON PATH and only codex is missing, so the raise can ONLY be the
        chosen-codex-cli-missing case.
        """
        # codex missing, claude (and all else) present — patch BOTH the gate's
        # shutil and discovery's (the resolver's PATH lane) for determinism.
        monkeypatch.setattr(ad.shutil, "which", _which_missing("codex"))
        monkeypatch.setattr(discovery.shutil, "which", _which_missing("codex"))
        settings = _settings(nodegen_llm_provider="codex-cli")

        with pytest.raises(AkmsDispatchError) as ei:
            require_node_gen_binary(settings)
        msg = str(ei.value)
        assert "codex" in msg, msg
        assert "codex-cli" in msg, msg

    @pytest.mark.integration
    def test_gate_returns_binary_when_chosen_cli_present(self, monkeypatch):
        """Bound the gate: a chosen CLI whose binary IS on PATH returns that binary
        (no raise) — so the gate only fails when the binary is genuinely absent.
        """
        monkeypatch.setattr(ad.shutil, "which", lambda binary: "/usr/bin/" + binary)
        settings = _settings(nodegen_llm_provider="codex-cli")
        assert require_node_gen_binary(settings) == "codex"

    @pytest.mark.regression
    def test_malformed_custom_clis_field_degrades_not_raises(self, monkeypatch):
        """Verifies: a non-dict-coercible learn_llm_custom_clis (e.g. a string)
        degrades to the claude fallback WITHOUT raising — never a 500 from the
        _custom_clis() dict() coercion on the node-gen request path.

        Requirement: degrade gracefully (belt-and-suspenders). Pydantic
        validates learn_llm_custom_clis as a dict for REAL Settings, so this is
        unreachable in production — but the invariant is 'ANY bad config never 500s', and the
        gate's classification belt must catch the TypeError/ValueError from
        dict('not-a-dict') and fall back, mirroring the resolver's whole-body belt.

        Non-vacuity: the choice 'my-tool' is not a built-in CLI / akms / nlm, so the
        gate REACHES _custom_clis(settings) — and learn_llm_custom_clis being a bare
        string makes dict(str) raise ValueError. A sanity assert proves the coercion
        WOULD raise; the gate returning 'claude' positively pins the belt (without it
        the exception propagates → 500). Empirically confirmed non-vacuous: removing
        the classification try/except makes this test raise ValueError; restored.
        """
        # Everything on PATH so a missing binary cannot be the reason for anything.
        monkeypatch.setattr(ad.shutil, "which", lambda binary: "/usr/bin/" + binary)

        # Sanity: the coercion _custom_clis performs (dict(value or {})) WOULD raise
        # on a bare string — proving the malformed-field case is real and that a
        # regression (removing the belt) would 500 here.
        with pytest.raises(ValueError):
            dict("not-a-dict")  # noqa: C418 — intentional: prove the coercion raises

        # Stub Settings WITHOUT effective_llm_provider (takes the getattr branch)
        # and with a malformed (string) learn_llm_custom_clis that reaches
        # _custom_clis() via the custom-CLI classification branch.
        settings = _settings(
            nodegen_llm_provider="my-tool",
            learn_llm_custom_clis="not-a-dict",
        )

        # Degrades to the claude fallback WITHOUT raising (no 400 — can't name a
        # binary; no 500 — the belt caught the coercion error).
        assert require_node_gen_binary(settings) == "claude"

    # ------------------------------------------------------------------ #
    # Route-level degrade-gracefully regression:
    # a malformed PERSISTED config must not 500 the run route. Settings.load()
    # lives INSIDE create_run's try; a bad config maps to a 4xx, never a 500.
    # Previously Settings was loaded OUTSIDE the try, so a
    # config error escaped as a 500 and bypassed require_node_gen_binary's belt.
    # ------------------------------------------------------------------ #
    @pytest.mark.integration
    def test_malformed_persisted_config_wrong_type_returns_4xx_not_500(self, akms_app):
        """Verifies: a persisted config whose ``learn_llm_custom_clis`` is a STRING
        (not a dict) — well-formed TOML, pydantic type error — yields a client 4xx
        from POST /runs, never a 500; the sidecar stays up.

        Non-vacuity: pre-fix
        (Settings.load() outside the try) the pydantic ValidationError escaped → 500;
        with the fix it is caught and mapped to 400.
        """
        app, ws, mp = akms_app
        bad_toml = ws.parent / "bad-type-config.toml"
        bad_toml.write_text('learn_llm_custom_clis = "not-a-dict"\n', encoding="utf-8")
        mp.setenv("LOGIC_LOOM_CONFIG", str(bad_toml))

        with TestClient(app) as client:
            resp = client.post(
                "/api/akms/node-generation/runs",
                json={"batch_id": "batch-1", "dry_run": False},
            )
            assert resp.status_code != 500, resp.text
            assert 400 <= resp.status_code < 500, resp.text
            # Sidecar stays up.
            assert client.get("/api/akms/node-generation/runs/nope").status_code == 404

    @pytest.mark.integration
    def test_malformed_persisted_config_bad_toml_returns_4xx_not_500(self, akms_app):
        """Verifies: a persisted config with malformed TOML *syntax*
        (TOMLDecodeError, not a pydantic ValidationError) also degrades to a client
        4xx from POST /runs, never a 500 (TOML load failures map to non-500). The sidecar stays up.
        """
        app, ws, mp = akms_app
        bad_toml = ws.parent / "bad-syntax-config.toml"
        # Unterminated string → tomllib.TOMLDecodeError at Settings.load().
        bad_toml.write_text('akms_repo = "unterminated\n', encoding="utf-8")
        mp.setenv("LOGIC_LOOM_CONFIG", str(bad_toml))

        with TestClient(app) as client:
            resp = client.post(
                "/api/akms/node-generation/runs",
                json={"batch_id": "batch-1", "dry_run": False},
            )
            assert resp.status_code != 500, resp.text
            assert 400 <= resp.status_code < 500, resp.text
            assert client.get("/api/akms/node-generation/runs/nope").status_code == 404
