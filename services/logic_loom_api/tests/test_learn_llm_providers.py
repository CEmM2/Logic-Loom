"""Tests for the Learn LLM agent-CLI providers.

Covers the transport (`_run_cli_template`), discovery/registration, key→env
injection, the provider section shape, the de-risked resolution precedence
(CLI agents activate only on an explicit choice — never on mere PATH presence),
and the `GET /api/learn/providers` endpoint. PATH is monkeypatched so results
do not depend on what happens to be installed on the test host.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from logic_loom_api import learn_llm_providers as p
from logic_loom_api.config import Settings
from logic_loom_api.deps import reload_settings
from logic_loom_api.main import api


def _which_only(*present: str):
    """A `shutil.which` stub: resolves only the named binaries."""
    names = set(present)
    return lambda binary: f"/usr/bin/{binary}" if binary in names else None


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
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


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
class TestRunCliTemplate:
    def test_prompt_substituted_as_arg(self) -> None:
        assert p._run_cli_template("echo {prompt}", "hello there") == "hello there"

    def test_prompt_piped_to_stdin(self) -> None:
        assert p._run_cli_template("cat", "piped body") == "piped body"

    def test_missing_binary_raises(self) -> None:
        with pytest.raises(RuntimeError, match="not found on PATH"):
            p._run_cli_template("definitely-not-a-real-bin-xyz", "x")

    def test_nonzero_exit_raises(self) -> None:
        with pytest.raises(RuntimeError, match="rc="):
            p._run_cli_template("false", "x")


class TestNormalizeOutput:
    def test_plain_passthrough(self) -> None:
        assert p._normalize_cli_output("  hello world  ") == "hello world"

    def test_unwraps_full_code_fence(self) -> None:
        assert p._normalize_cli_output("```\nthe answer\n```") == "the answer"

    def test_unwraps_fence_with_lang_tag(self) -> None:
        assert p._normalize_cli_output("```text\nbody\nmore\n```") == "body\nmore"

    def test_keeps_inline_backticks(self) -> None:
        # not a full-output fence → content is left untouched
        assert p._normalize_cli_output("use `ti.field` here") == "use `ti.field` here"

    def test_collapses_blank_line_runs(self) -> None:
        assert p._normalize_cli_output("a\n\n\n\nb") == "a\n\nb"


# --------------------------------------------------------------------------- #
# Discovery / registration / key injection
# --------------------------------------------------------------------------- #
class TestDiscovery:
    def test_available_builtins_on_path(self, monkeypatch) -> None:
        monkeypatch.setattr(p.shutil, "which", _which_only("claude"))
        assert p.available_cli_providers(Settings()) == ["claude-cli"]

    def test_available_includes_configured_custom(self, monkeypatch) -> None:
        monkeypatch.setattr(p.shutil, "which", _which_only("codex", "mytool"))
        s = Settings(learn_llm_custom_clis={"copilot": "", "mine": "mytool {prompt}"})
        names = p.available_cli_providers(s)
        assert "codex-cli" in names
        assert "mine" in names  # configured + binary on PATH
        assert "copilot" not in names  # empty command

    def test_discover_marks_api_by_key(self, monkeypatch) -> None:
        monkeypatch.setattr(p.shutil, "which", _which_only())  # no CLIs
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        rows = {r["name"]: r for r in p.discover_providers(Settings(anthropic_api_key="sk-x"))}
        assert rows["akms"]["route"] == "api" and rows["akms"]["available"] is True
        assert rows["claude-cli"]["available"] is False

    def test_malformed_custom_cli_degrades_not_raises(self, monkeypatch) -> None:
        # A user-entered custom CLI with an unbalanced quote (ConfigUpdate accepts
        # any string) must NOT raise shlex.ValueError out of discovery/availability
        # — it degrades to an unavailable row (CLAUDE.md degrade-gracefully).
        # Regression test for a malformed-custom-CLI crash found in review.
        monkeypatch.setattr(p.shutil, "which", _which_only("mytool"))
        s = Settings(learn_llm_custom_clis={"bad": '"unterminated', "ok": "mytool {prompt}"})
        rows = {r["name"]: r for r in p.discover_providers(s)}  # must not raise
        assert rows["bad"]["available"] is False
        assert "invalid" in rows["bad"]["detail"]
        assert rows["ok"]["available"] is True  # a valid custom CLI still resolves
        # the availability helper (used by resolve_choice's CLI path) also degrades
        names = p.available_cli_providers(s)
        assert "bad" not in names and "ok" in names

    def test_inject_keys_into_env(self, monkeypatch) -> None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        try:
            injected = p.inject_api_keys_into_env(Settings(google_api_key="g-key"))
            assert "GOOGLE_API_KEY" in injected
            assert os.environ.get("GOOGLE_API_KEY") == "g-key"
        finally:
            os.environ.pop("GOOGLE_API_KEY", None)

    def test_inject_no_force_keeps_existing(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "preset")
        try:
            injected = p.inject_api_keys_into_env(Settings(anthropic_api_key="new"))
            assert "ANTHROPIC_API_KEY" not in injected
            assert os.environ["ANTHROPIC_API_KEY"] == "preset"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_inject_force_overwrites(self, monkeypatch) -> None:
        # The reload path: a changed key must overwrite a stale env value.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "old")
        try:
            injected = p.inject_api_keys_into_env(
                Settings(anthropic_api_key="new"), force=True
            )
            assert "ANTHROPIC_API_KEY" in injected
            assert os.environ["ANTHROPIC_API_KEY"] == "new"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)


# --------------------------------------------------------------------------- #
# Provider section shape (needs akms_learn)
# --------------------------------------------------------------------------- #
class TestProvider:
    def test_provider_builds_valid_section(self) -> None:
        pytest.importorskip("akms_learn")
        provider = p._make_provider("claude-cli", "cat")  # cat echoes the prompt
        sections = provider("Yield surfaces", ["n1", "n2"])
        assert len(sections) == 1
        sec = sections[0]
        assert sec.source_node_ids == ("n1", "n2")
        assert sec.validation_status == "valid"
        assert sec.generator == "claude-cli"
        assert sec.id == "n1::claude-cli"
        assert "Yield surfaces" in sec.content  # prompt echoed back through cat

    def test_provider_empty_ids_returns_empty(self) -> None:
        pytest.importorskip("akms_learn")
        assert p._make_provider("codex-cli", "cat")("topic", []) == []

    def test_register_only_on_path_builtins(self, monkeypatch) -> None:
        pytest.importorskip("akms_learn")
        from akms_learn.llm import registry

        monkeypatch.setattr(p.shutil, "which", _which_only("claude"))
        names = p.register_host_llm_providers(Settings())
        assert "claude-cli" in names and "codex-cli" not in names
        assert "claude-cli" in registry._REGISTRY


# --------------------------------------------------------------------------- #
# Resolution precedence (the de-risked CRITICAL edit)
# --------------------------------------------------------------------------- #
class TestResolution:
    def test_explicit_cli_resolves_when_on_path(self, monkeypatch) -> None:
        from logic_loom_api.adapters import learn_adapter as la

        monkeypatch.setattr(p.shutil, "which", _which_only("claude"))
        prov = la.resolve_host_llm_provider(Settings(learn_llm_provider="claude-cli"))
        assert prov is not None and prov.provider == "claude-cli"

    def test_explicit_cli_off_path_returns_none(self, monkeypatch) -> None:
        from logic_loom_api.adapters import learn_adapter as la

        monkeypatch.setattr(p.shutil, "which", _which_only())  # nothing on PATH
        assert (
            la.resolve_host_llm_provider(Settings(learn_llm_provider="claude-cli"))
            is None
        )

    def test_no_choice_never_autoselects_cli(self, monkeypatch) -> None:
        # The crux: CLI agents on PATH must NOT flip the gate without a choice.
        from logic_loom_api.adapters import learn_adapter as la

        monkeypatch.setattr(p.shutil, "which", _which_only("claude", "codex"))
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert la.resolve_host_llm_provider(Settings()) is None
        assert la.host_llm_modes_available(Settings()) is False


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
def test_providers_endpoint(client: TestClient) -> None:
    r = client.get("/api/learn/providers")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["providers"], list)
    names = {row["name"] for row in body["providers"]}
    assert "akms" in names and "claude-cli" in names and "nlm" in names
    assert "selected" in body


def test_providers_reload_endpoint(client: TestClient) -> None:
    r = client.post("/api/learn/providers/reload")
    assert r.status_code == 200
    body = r.json()
    assert body["reloaded"] is True
    assert isinstance(body["providers"], list)
    assert "injected" in body and "registered" in body
