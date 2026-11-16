"""Reset and backup semantics audit for the AKMS Claude assets.

Asserts the audit guarantee documented on
:func:`logic_loom_api.akms_claude_assets.install_editable_assets`:

    After a reset (overwrite=True), the prior user tree is recoverable from
    a sibling ``claude-assets.backup/`` directory. Edits are therefore never
    silently destroyed by a single reset.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from logic_loom_api import akms_claude_assets as aca


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirect user_config_dir() to a tmp dir for the duration of the test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    expected = (
        (tmp_path / "AppData" / "Roaming" / "Logic-Loom" / "claude-assets")
        if sys.platform == "win32"
        else (tmp_path / "logic-loom" / "claude-assets")
    )
    return expected


class TestClaudeAssetsResetBackup:
    """Reset/backup semantics: prior edits are recoverable after a reset."""

    @pytest.mark.unit
    def test_reset_preserves_previous_in_backup(self, isolated_config):
        """A marker file inside the user tree survives in claude-assets.backup/
        after install_editable_assets(overwrite=True), so previous edits are
        recoverable from claude-assets.backup/.
        """
        target = aca.install_editable_assets()
        # User drops a marker into the editable tree.
        marker_rel = Path(".claude") / "USER_MARKER.txt"
        marker_path = target / marker_rel
        marker_path.write_text("preserve me", encoding="utf-8")
        assert marker_path.is_file()

        # Reset.
        aca.install_editable_assets(overwrite=True)

        # The original location is now bundled-fresh again — marker gone.
        assert not marker_path.exists(), "marker must be cleared from active tree"
        # …but it must live on in the sibling backup.
        backup = target.with_name("claude-assets.backup")
        assert backup.is_dir(), "backup sibling must exist after reset"
        recovered = backup / marker_rel
        assert recovered.is_file(), "marker must survive in the backup"
        assert recovered.read_text(encoding="utf-8") == "preserve me"

        # And the bundled tree was actually restored at the original location.
        assert (target / ".claude" / "skills" / "node-gen" / "SKILL.md").is_file()

    @pytest.mark.unit
    def test_second_reset_replaces_prior_backup(self, isolated_config):
        """Successive resets clobber the prior backup (documented limitation)."""
        target = aca.install_editable_assets()
        marker_a = target / ".claude" / "FIRST.txt"
        marker_a.write_text("first", encoding="utf-8")
        aca.install_editable_assets(overwrite=True)
        backup = target.with_name("claude-assets.backup")
        assert (backup / ".claude" / "FIRST.txt").is_file()

        # Edit again, reset again.
        marker_b = target / ".claude" / "SECOND.txt"
        marker_b.write_text("second", encoding="utf-8")
        aca.install_editable_assets(overwrite=True)
        # FIRST.txt is now gone from the backup (prior backup was deleted).
        assert not (backup / ".claude" / "FIRST.txt").exists()
        # SECOND.txt survived from the most recent edit cycle.
        assert (backup / ".claude" / "SECOND.txt").is_file()
