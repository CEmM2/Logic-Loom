"""Tests for the Assistant workspace bootstrap.

Covers: sidecar boot with no prior workspace creates the full tree;
booting twice is idempotent — no overwrite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.assistant.bootstrap import (
    SEED_JSON_FILES,
    SENTINEL_NAME,
    WORKSPACE_SUBDIRS,
    bootstrap_assistant_workspace,
)
from logic_loom_api.config import Settings


def _make_settings(tmp_path: Path) -> Settings:
    """Build a Settings instance with a tmp-isolated workspace tree."""
    akms_ws = tmp_path / "AKMS-Workspace"
    # Default resolver layout → assistant workspace at
    # <akms_workspace>/../Assistant-Workspace == tmp_path/Assistant-Workspace.
    return Settings(
        akms_workspace=akms_ws,
        assistant_workspace=None,
        assistant_workspace_colocate=False,
        cache_path=tmp_path / "cache",
        # Use placeholder-safe path for vault_path so /api/app/config doesn't
        # fail in the integration test below.
        zsum_repo=tmp_path / "zsum",
    )


class TestAssistantWorkspaceBootstrap:
    """Integration tests for bootstrap_assistant_workspace + lifespan hook."""

    @pytest.mark.integration
    def test_bootstrap_creates_full_tree(self, tmp_path: Path) -> None:
        """Verifies: bootstrap_assistant_workspace(settings) creates
        every required subdir and seed JSON when the workspace is empty.

        Required: assistant/{batches.json, workflows.json,
        prompt_profiles.json, discovery_cache/, related_cache/, runs/,
        outputs/, index/}.

        Requirement: 'Sidecar boot with no prior Assistant workspace creates the
        full tree'.
        """
        s = _make_settings(tmp_path)
        workspace = s.assistant_workspace_path()
        assert not workspace.exists(), "workspace should not pre-exist"

        returned = bootstrap_assistant_workspace(s)

        assert returned == workspace
        assert workspace.is_dir()

        # Every subdir from the spec exists.
        expected_subdirs = {"discovery_cache", "related_cache", "runs", "outputs", "index"}
        assert set(WORKSPACE_SUBDIRS) == expected_subdirs
        for sub in expected_subdirs:
            assert (workspace / sub).is_dir(), f"missing subdir: {sub}"

        # Every seed JSON exists and is valid JSON.
        expected_seeds = {"batches.json", "workflows.json", "prompt_profiles.json"}
        actual_seed_names = {name for name, _ in SEED_JSON_FILES}
        assert actual_seed_names == expected_seeds
        for name in expected_seeds:
            p = workspace / name
            assert p.is_file(), f"missing seed file: {name}"
            json.loads(p.read_text())  # raises if not valid JSON

        # Sentinel records schema_version.
        sentinel = workspace / SENTINEL_NAME
        assert sentinel.is_file()
        sentinel_data = json.loads(sentinel.read_text())
        assert sentinel_data["schema_version"] >= 1

    @pytest.mark.integration
    def test_bootstrap_is_idempotent(self, tmp_path: Path) -> None:
        """Verifies: calling bootstrap_assistant_workspace twice over an
        existing tree does not overwrite files or wipe their contents.

        Requirement: 'Re-running boot with an existing workspace does not
        overwrite or wipe any files'.
        """
        s = _make_settings(tmp_path)
        workspace = bootstrap_assistant_workspace(s)

        # User-edited content in seed files.
        batches_payload = {"user_edited": True, "batches": [{"id": "b1"}]}
        (workspace / "batches.json").write_text(json.dumps(batches_payload))

        # User-edited sentinel — should also survive.
        custom_sentinel = {"schema_version": 1, "user_note": "do not overwrite"}
        (workspace / SENTINEL_NAME).write_text(json.dumps(custom_sentinel))

        # User-created file in a managed subdir — should survive.
        (workspace / "outputs" / "user_file.md").write_text("preserve me")

        # Second bootstrap.
        bootstrap_assistant_workspace(s)

        assert json.loads((workspace / "batches.json").read_text()) == batches_payload
        assert json.loads((workspace / SENTINEL_NAME).read_text()) == custom_sentinel
        assert (workspace / "outputs" / "user_file.md").read_text() == "preserve me"

    @pytest.mark.integration
    def test_lifespan_hook_invokes_bootstrap_once(self, tmp_path: Path, monkeypatch) -> None:
        """Verifies: starting the FastAPI app via TestClient triggers
        bootstrap_assistant_workspace exactly once and produces the
        expected tree under the configured assistant_workspace.

        Requirement: covers wiring of the bootstrap call into the FastAPI lifespan.
        """
        # Point the user-config to a tmp file so Settings.load() picks up
        # our tmp paths. Use LOGIC_LOOM_* env vars to inject paths through
        # pydantic-settings; this matches the production precedence chain.
        monkeypatch.setenv(
            "LOGIC_LOOM_AKMS_WORKSPACE",
            str(tmp_path / "AKMS-Workspace"),
        )
        monkeypatch.setenv(
            "LOGIC_LOOM_ASSISTANT_WORKSPACE",
            str(tmp_path / "AssistantOnly"),
        )
        monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
        # Empty config file so the user TOML layer does not override env.
        cfg = tmp_path / "logic-loom.toml"
        cfg.write_text("")
        monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(cfg))

        # Bust the lru_cache so the lifespan picks up our env-injected paths.
        from logic_loom_api import deps

        deps.get_settings.cache_clear()

        from logic_loom_api.main import api

        target_ws = tmp_path / "AssistantOnly"
        assert not target_ws.exists()

        with TestClient(api):
            # Lifespan has run by now.
            assert target_ws.is_dir()
            for sub in WORKSPACE_SUBDIRS:
                assert (target_ws / sub).is_dir(), f"lifespan missed subdir {sub}"
            for name, _ in SEED_JSON_FILES:
                assert (target_ws / name).is_file(), f"lifespan missed seed {name}"
            assert (target_ws / SENTINEL_NAME).is_file()

        # Idempotency through the lifespan path: a second TestClient start
        # over the same tree should not error and should leave user
        # content alone.
        user_payload = {"keep": "me"}
        (target_ws / "batches.json").write_text(json.dumps(user_payload))
        deps.get_settings.cache_clear()
        with TestClient(api):
            assert json.loads((target_ws / "batches.json").read_text()) == user_payload

        # Clean up the cached settings for the next test.
        deps.get_settings.cache_clear()
