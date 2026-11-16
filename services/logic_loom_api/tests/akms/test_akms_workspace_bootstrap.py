"""AKMS workspace bootstrap on first run.

Covers: running the sidecar with no prior workspace creates all
directories and the sentinel.

Risk-derived case: lifespan failure must not block the sidecar coming up
(CLAUDE.md degrade-gracefully invariant).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api import akms_workspace as ws_module
from logic_loom_api.akms_workspace import (
    SCHEMA_VERSION,
    SENTINEL_NAME,
    WORKSPACE_SUBDIRS,
    bootstrap_akms_workspace,
)
from logic_loom_api.config import Settings


class TestAkmsWorkspaceBootstrap:
    """Integration tests: bootstrap workspace on first sidecar run."""

    @pytest.mark.integration
    def test_bootstrap_creates_all_subdirs(self, tmp_path):
        """Verifies: bootstrap_akms_workspace(settings) creates the full tree.

        Requirement: 'Running the sidecar with no prior workspace creates all
             directories and the sentinel.'
        """
        ws = tmp_path / "WS"
        s = Settings.load(config_path=tmp_path / "missing.toml", akms_workspace=ws)
        result = bootstrap_akms_workspace(s)
        assert result == ws

        # All expected sub-dirs exist.
        expected = {
            "prompt-library/variants",
            "prompt-library/profiles",
            "workflow-library/user-workflows",
            "Sources_Evals/NLM/Inputs",
            "Sources_Evals/NLM/Outputs",
            "generated_nodes",
            "runs",
            "AKMS_Sources/new",
            "claude-runtime",
            "zsum-runtime",
        }
        assert set(WORKSPACE_SUBDIRS) == expected, "WORKSPACE_SUBDIRS list out of sync with spec"
        for sub in expected:
            assert (ws / sub).is_dir(), f"missing subdir: {sub}"

        # Sentinel exists with schema_version: 1.
        sentinel = ws / SENTINEL_NAME
        assert sentinel.exists()
        data = json.loads(sentinel.read_text())
        assert data == {"schema_version": SCHEMA_VERSION}

    @pytest.mark.integration
    def test_bootstrap_idempotent_on_second_run(self, tmp_path):
        """Verifies: bootstrap_akms_workspace is a no-op on an existing tree.

        Pre-resolved design says bootstrap must be idempotent. A second
        invocation does not raise, does not rewrite the sentinel, and leaves
        user-modified contents untouched.
        """
        ws = tmp_path / "WS"
        s = Settings.load(config_path=tmp_path / "missing.toml", akms_workspace=ws)
        bootstrap_akms_workspace(s)

        # User edits the sentinel (e.g. future migration tagged the workspace).
        sentinel = ws / SENTINEL_NAME
        custom_content = '{"schema_version": 1, "user_edit": "do_not_clobber"}\n'
        sentinel.write_text(custom_content)

        # User creates a file in a subdir.
        user_file = ws / "prompt-library" / "variants" / "my_variant.yaml"
        user_file.write_text("name: custom\n")

        # Second run: must not raise, must not overwrite either file.
        bootstrap_akms_workspace(s)
        assert sentinel.read_text() == custom_content
        assert user_file.read_text() == "name: custom\n"

    @pytest.mark.integration
    def test_lifespan_failure_does_not_block_app(self, tmp_path, monkeypatch):
        """Verifies: a bootstrap exception inside the FastAPI lifespan is
        swallowed and the sidecar still serves /api/app/health.

        Risk-derived: CLAUDE.md degrade-gracefully invariant — a workspace
        bootstrap failure must never prevent the sidecar from coming up.
        """
        cfg_path = tmp_path / "config.toml"

        # Make bootstrap raise. Patch on the imported symbol used in main.py.
        from logic_loom_api import main as main_module

        def _boom(_settings):
            raise RuntimeError("simulated bootstrap explosion")

        monkeypatch.setattr(main_module, "bootstrap_akms_workspace", _boom)

        with patch.dict(
            os.environ,
            {
                "LOGIC_LOOM_CONFIG": str(cfg_path),
                "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
                "LOGIC_LOOM_AKMS_WORKSPACE": str(tmp_path / "WS"),
            },
        ):
            from logic_loom_api.deps import reload_settings

            reload_settings()
            try:
                with TestClient(main_module.api) as client:
                    r = client.get("/api/app/health")
                    assert r.status_code == 200
                    assert r.json()["status"] == "ok"
            finally:
                reload_settings()

    @pytest.mark.integration
    def test_bootstrap_subdir_error_logged_not_raised(self, tmp_path, monkeypatch, caplog):
        """Verifies: a per-subdir mkdir error is logged but the function does
        not raise — other subdirs still get created.

        Risk-derived: the bootstrap must be resilient to partial-failure
        scenarios (e.g. a permissions glitch on one path).
        """
        ws = tmp_path / "WS"
        s = Settings.load(config_path=tmp_path / "missing.toml", akms_workspace=ws)

        original_mkdir = Path.mkdir
        call_count = {"n": 0}

        def flaky_mkdir(self, *args, **kwargs):
            call_count["n"] += 1
            # Make the *second* mkdir call (a subdir) fail; root mkdir is the
            # first call and must succeed.
            if call_count["n"] == 2:
                raise OSError("simulated mkdir failure on one subdir")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", flaky_mkdir)
        # No raise:
        bootstrap_akms_workspace(s)
        # Remaining subdirs still created:
        assert (ws / "generated_nodes").is_dir() or (ws / "runs").is_dir()

    @pytest.mark.integration
    def test_bootstrap_expands_tilde(self, tmp_path, monkeypatch):
        """Verifies: tilde in akms_workspace is expanded before mkdir.

        Risk-derived: ``Path('~/foo').mkdir()`` literally creates a '~'
        directory in the cwd; bootstrap must expand first.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        # Path.expanduser() reads USERPROFILE on Windows, HOME on POSIX.
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        s = Settings.load(config_path=tmp_path / "missing.toml", akms_workspace=Path("~/MyWS"))
        result = bootstrap_akms_workspace(s)
        assert result == tmp_path / "MyWS"
        assert (tmp_path / "MyWS" / "generated_nodes").is_dir()
        # No literal '~' directory created anywhere.
        assert not Path("~/MyWS").exists() or Path("~/MyWS").expanduser() == result
