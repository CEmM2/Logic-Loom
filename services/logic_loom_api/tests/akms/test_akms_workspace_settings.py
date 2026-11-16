"""akms_workspace setting and its resolver helpers.

Covers: akms_workspace exists on Settings.load(), and the resolver helpers
return correctly composed paths.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from logic_loom_api.config import Settings


class TestAkmsWorkspaceSettings:
    """Unit tests: akms_workspace setting + resolver helpers."""

    @pytest.mark.unit
    def test_akms_workspace_defaults(self, tmp_path):
        """Verifies: Settings.load().akms_workspace defaults to the
        platform application-data path (not ~/Documents — macOS TCC).

        Requirement: 'Settings.load().akms_workspace exists'.
        """
        from logic_loom_api.config import _default_akms_workspace

        # Use a missing TOML and clear any LOGIC_LOOM_AKMS_WORKSPACE env so the
        # default kicks in.
        env_clean = {k: v for k, v in os.environ.items() if k != "LOGIC_LOOM_AKMS_WORKSPACE"}
        with patch.dict(os.environ, env_clean, clear=True):
            s = Settings.load(config_path=tmp_path / "missing.toml")
        assert s.akms_workspace == _default_akms_workspace()

    @pytest.mark.unit
    def test_akms_workspace_overridable_via_env(self, tmp_path):
        """Verifies: LOGIC_LOOM_AKMS_WORKSPACE env var overrides the default.

        Requirement: settings-precedence (env > defaults).
        """
        override = tmp_path / "MyWorkspace"
        with patch.dict(os.environ, {"LOGIC_LOOM_AKMS_WORKSPACE": str(override)}):
            s = Settings.load(config_path=tmp_path / "missing.toml")
        assert s.akms_workspace == override

    @pytest.mark.unit
    def test_akms_workspace_overridable_via_kwargs(self, tmp_path):
        """Verifies: kwargs override env (precedence kwargs > env)."""
        env_override = tmp_path / "env_ws"
        kw_override = tmp_path / "kw_ws"
        with patch.dict(os.environ, {"LOGIC_LOOM_AKMS_WORKSPACE": str(env_override)}):
            s = Settings.load(config_path=tmp_path / "missing.toml", akms_workspace=kw_override)
        assert s.akms_workspace == kw_override

    @pytest.mark.unit
    def test_resolver_helpers_return_expected_subpaths(self, tmp_path):
        """Verifies: every resolver helper composes paths under akms_workspace.

        Requirement: 'Resolver helpers return correctly composed paths'.
        """
        ws = tmp_path / "WS"
        s = Settings.load(config_path=tmp_path / "missing.toml", akms_workspace=ws)
        assert s.akms_batches_path() == ws / "batches.json"
        assert s.akms_batch_state_path() == ws / "batch_assignments.json"
        assert s.akms_saved_queries_path() == ws / "saved_queries.json"
        assert s.akms_nlm_inputs_dir() == ws / "Sources_Evals" / "NLM" / "Inputs"
        assert s.akms_nlm_outputs_dir() == ws / "Sources_Evals" / "NLM" / "Outputs"
        assert s.akms_generated_nodes_dir() == ws / "generated_nodes"

    @pytest.mark.unit
    def test_akms_repo_description_dev_only(self):
        """Verifies: akms_repo description flags it as development-only.

        The description must say akms_repo is development-only and not
        required in packaged builds.
        """
        field = Settings.model_fields["akms_repo"]
        desc = (field.description or "").lower()
        assert "development-only" in desc or "dev-only" in desc
        assert "packaged" in desc

    @pytest.mark.unit
    def test_resolver_helpers_expand_user_tilde(self, tmp_path, monkeypatch):
        """Verifies: resolver helpers expand ``~`` so callers get a concrete Path.

        Risk-derived: downstream code calls ``.mkdir()`` on these paths and
        ``Path('~/foo').mkdir()`` will create a literal '~' directory.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        # Path.expanduser() reads USERPROFILE on Windows, HOME on POSIX.
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        s = Settings.load(config_path=tmp_path / "missing.toml", akms_workspace=Path("~/Documents/LL/WS"))
        result = s.akms_batches_path()
        assert "~" not in str(result)
        assert result == tmp_path / "Documents" / "LL" / "WS" / "batches.json"
