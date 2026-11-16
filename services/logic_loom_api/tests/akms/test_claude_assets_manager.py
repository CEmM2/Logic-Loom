"""AKMS Claude asset manager module.

Exercises ``logic_loom_api.akms_claude_assets`` against the bundled resource
tree materialised by ``scripts/sync_akms_claude_assets.py``. The
tests redirect ``XDG_CONFIG_HOME``/``APPDATA`` to ``tmp_path`` so they never
touch the real user config.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from logic_loom_api import akms_claude_assets as aca


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirect user_config_dir() to a tmp dir.

    Returns the would-be ``user_assets_dir`` Path (created on demand by the
    callee). Does *not* pre-create it; that is the call-under-test's job.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # On Windows the override is APPDATA; set both to be safe in CI.
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    expected_user_dir = (
        (tmp_path / "AppData" / "Roaming" / "Logic-Loom" / "claude-assets")
        if sys.platform == "win32"
        else (tmp_path / "logic-loom" / "claude-assets")
    )
    return expected_user_dir


class TestClaudeAssetsManager:
    """Asset manager: path safety, install/reset, user-or-bundled reads, status."""

    # ----- safe_asset_path: negative paths (AC #1 — 100% coverage) -----

    @pytest.mark.unit
    def test_safe_asset_path_rejects_absolute(self):
        """ValueError when the path is absolute (POSIX or Windows)."""
        with pytest.raises(ValueError, match="absolute"):
            aca.safe_asset_path("/etc/passwd")
        # Empty string is a related negative path (covers the early guard).
        with pytest.raises(ValueError, match="empty"):
            aca.safe_asset_path("")

    @pytest.mark.unit
    def test_safe_asset_path_rejects_traversal(self):
        """ValueError when any segment is `..`."""
        with pytest.raises(ValueError, match="traversal"):
            aca.safe_asset_path(".claude/../../etc/passwd")
        with pytest.raises(ValueError, match="traversal"):
            aca.safe_asset_path("../scripts/x.py")

    @pytest.mark.unit
    def test_safe_asset_path_rejects_non_claude_or_scripts(self):
        """ValueError when the first segment is outside the whitelist."""
        with pytest.raises(ValueError, match=r"\.claude"):
            aca.safe_asset_path("etc/passwd")
        with pytest.raises(ValueError, match=r"\.claude"):
            aca.safe_asset_path("nodes_vault/x.md")

    # ----- safe_asset_path: happy paths -----

    @pytest.mark.unit
    def test_safe_asset_path_accepts_skill_md(self):
        """A well-formed `.claude/...` skill path round-trips as Path."""
        rel = aca.safe_asset_path(".claude/skills/node-gen/SKILL.md")
        assert rel == Path(".claude/skills/node-gen/SKILL.md")
        # `scripts/` is also valid.
        rel2 = aca.safe_asset_path("scripts/akms_node_convert.py")
        assert rel2 == Path("scripts/akms_node_convert.py")

    # ----- install_editable_assets -----

    @pytest.mark.unit
    def test_install_copies_bundled(self, isolated_config):
        """install_editable_assets copies bundled tree under user_assets_dir."""
        target = aca.install_editable_assets()
        assert target == isolated_config
        assert target.exists()
        # Bundled SKILL.md must land at the expected path.
        assert (target / ".claude" / "skills" / "node-gen" / "SKILL.md").is_file()
        assert (target / "scripts" / "akms_node_convert.py").is_file()
        manifest = json.loads((target / aca.MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["created_from"] == "bundled"
        assert manifest["user_modified"] is False

    @pytest.mark.unit
    def test_reset_restores_bundled(self, isolated_config):
        """reset_user_asset rewrites a user-edited file with bundled content."""
        target = aca.install_editable_assets()
        skill_path = target / ".claude" / "skills" / "node-gen" / "SKILL.md"
        bundled_text = skill_path.read_text(encoding="utf-8")
        # User edits the file.
        skill_path.write_text("USER EDITED", encoding="utf-8")
        assert skill_path.read_text(encoding="utf-8") == "USER EDITED"
        # Reset that single file.
        aca.reset_user_asset(".claude/skills/node-gen/SKILL.md")
        assert skill_path.read_text(encoding="utf-8") == bundled_text
        # Manifest now reflects a user-driven modification.
        manifest = json.loads((target / aca.MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["user_modified"] is True

    # ----- supporting helpers -----

    @pytest.mark.unit
    def test_read_user_or_bundled_prefers_user(self, isolated_config):
        """read_user_or_bundled returns the user copy when present."""
        target = aca.install_editable_assets()
        skill_rel = ".claude/skills/node-gen/SKILL.md"
        # No user override yet — falls back to bundled (== same content as
        # install just copied).
        bundled_text = (target / ".claude" / "skills" / "node-gen" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert aca.read_user_or_bundled(skill_rel) == bundled_text
        # Now write a user override and verify precedence.
        aca.write_user_asset(skill_rel, "OVERRIDE")
        assert aca.read_user_or_bundled(skill_rel) == "OVERRIDE"

    @pytest.mark.unit
    def test_asset_status_reflects_installed_state(self, isolated_config):
        """asset_status returns installed=False before install and True after."""
        before = aca.asset_status()
        assert before["installed"] is False
        assert before["manifest"] is None
        aca.install_editable_assets()
        after = aca.asset_status()
        assert after["installed"] is True
        assert after["manifest"]["created_from"] == "bundled"
