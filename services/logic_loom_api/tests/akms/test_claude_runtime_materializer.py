"""AKMS Claude runtime materializer module.

Exercises :func:`logic_loom_api.akms_claude_runtime.materialize_runtime_workspace`
against both the bundled resources tree (no user assets) and a fixture user
assets dir. ``XDG_CACHE_HOME`` / ``LOCALAPPDATA`` and ``XDG_CONFIG_HOME`` /
``APPDATA`` are redirected to ``tmp_path`` so the tests never touch the real
user cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from logic_loom_api import akms_claude_assets as aca
from logic_loom_api import akms_claude_runtime as acr


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Redirect both config and cache to tmp_path/{config,cache}."""
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    cfg.mkdir(parents=True)
    cache.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    # Windows overrides — set even on POSIX so the test is portable.
    monkeypatch.setenv("APPDATA", str(cfg / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(cache / "AppData" / "Local"))
    return tmp_path


def _expected_runtime(tmp_path: Path) -> Path:
    if sys.platform == "win32":
        return (
            tmp_path
            / "cache"
            / "AppData"
            / "Local"
            / "Logic-Loom"
            / "claude-runtime"
            / acr.RUNTIME_VERSION
        )
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Caches"
            / "Logic-Loom"
            / "claude-runtime"
            / acr.RUNTIME_VERSION
        )
    return (
        tmp_path
        / "cache"
        / "logic-loom"
        / "claude-runtime"
        / acr.RUNTIME_VERSION
    )


class TestClaudeRuntimeMaterializer:
    """Runtime materializer: bundled vs user assets, overlays, force flag."""

    @pytest.mark.unit
    def test_materialize_without_overlays_uses_bundled(self, isolated_paths):
        """No user_assets_dir -> base copy comes from the bundled tree."""
        # Sanity: user_assets_dir must NOT pre-exist for this test.
        assert not aca.user_assets_dir().exists()
        path = acr.materialize_runtime_workspace(force=True)
        assert path == _expected_runtime(isolated_paths)
        # Requirement: canary SKILL.md exists at runtime root.
        assert (path / ".claude" / "skills" / "node-gen" / "SKILL.md").is_file()
        # Inputs/outputs/sentinel always present.
        assert (path / "inputs").is_dir()
        assert (path / "outputs").is_dir()
        sentinel = path / ".logic-loom-runtime-ok"
        assert sentinel.is_file()
        assert sentinel.read_text(encoding="utf-8").strip() == acr.RUNTIME_VERSION

    @pytest.mark.unit
    def test_materialize_with_user_assets_uses_user_copy(self, isolated_paths):
        """When user_assets_dir() exists, the runtime copies from it (not bundled)."""
        target = aca.install_editable_assets()
        # Introduce a marker only present in the user tree (not in the bundle).
        marker_rel = Path(".claude") / "skills" / "node-gen" / "SKILL.md"
        (target / marker_rel).write_text("USER MARKER", encoding="utf-8")
        # And add a totally new file outside the bundle to be extra-sure.
        extra_rel = Path(".claude") / "user_extra.md"
        (target / extra_rel).write_text("hello", encoding="utf-8")
        # Materialize.
        runtime = acr.materialize_runtime_workspace(force=True)
        assert (runtime / marker_rel).read_text(encoding="utf-8") == "USER MARKER"
        assert (runtime / extra_rel).read_text(encoding="utf-8") == "hello"

    @pytest.mark.unit
    def test_materialize_with_overlay_rewrites_target_file(self, isolated_paths):
        """effective_prompts byte-replaces the named file after the base copy."""
        runtime = acr.materialize_runtime_workspace(
            force=True,
            effective_prompts={
                ".claude/skills/node-gen/SKILL.md": "OVERLAY_CONTENT",
            },
        )
        skill = runtime / ".claude" / "skills" / "node-gen" / "SKILL.md"
        assert skill.read_text(encoding="utf-8") == "OVERLAY_CONTENT"

    @pytest.mark.unit
    def test_materialize_overlay_rejects_traversal(self, isolated_paths):
        """Overlay paths flow through safe_asset_path; `..` raises ValueError."""
        with pytest.raises(ValueError):
            acr.materialize_runtime_workspace(
                force=True,
                effective_prompts={"../escape.md": "x"},
            )

    @pytest.mark.unit
    def test_materialize_force_false_keeps_existing_tree(self, isolated_paths):
        """force=False preserves pre-existing runtime artifacts."""
        runtime = acr.materialize_runtime_workspace(force=True)
        stamp = runtime / "inputs" / "user-stamp.txt"
        stamp.write_text("preserved", encoding="utf-8")
        runtime2 = acr.materialize_runtime_workspace(force=False)
        assert runtime2 == runtime
        assert stamp.read_text(encoding="utf-8") == "preserved"
