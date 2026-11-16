"""Windows PowerShell sidecar build script.

Linux harness cannot run PowerShell end-to-end — these tests assert
static structural invariants the PS script must satisfy. Live
execution happens on the Windows CI runner.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
PS_SCRIPT = ROOT / "scripts" / "build_sidecar_windows.ps1"


class TestBuildSidecarWindowsScript:
    """Static contract of the Windows sidecar build script."""

    def test_script_exists(self) -> None:
        """Verifies: PowerShell build script is present."""
        assert PS_SCRIPT.exists(), f"{PS_SCRIPT} not found"

    def test_script_needs_no_source_checkout(self) -> None:
        """Verifies: the build reads nothing from a vendored source tree.

        Requirement: parity with the macOS/Linux build scripts.
        """
        text = PS_SCRIPT.read_text()
        assert "thirdparty" not in text, (
            "windows script must not depend on a vendored thirdparty/ tree"
        )

    def test_script_installs_from_the_lockfile(self) -> None:
        """Verifies: dependencies come from `uv export --locked`, by extra name."""
        text = PS_SCRIPT.read_text()
        assert '"export"' in text and '"--locked"' in text
        assert '$SidecarExtras = @("akms", "mechdsl", "failure-memory", "records")' in text
        assert "akms[" not in text, (
            "windows script restates an AKMS requirement; declare it in "
            "services/logic_loom_api/pyproject.toml instead"
        )

    def test_script_calls_sync_inside_full_bundle_branch(self) -> None:
        """Verifies: the asset sync runs in the full-bundle branch, after the
        dependency install and before logic-loom-api is installed.
        """
        text = PS_SCRIPT.read_text()
        assert "sync_akms_claude_assets.py" in text, (
            "windows script does not invoke sync_akms_claude_assets.py"
        )
        install_idx = text.find("& uv pip install pyinstaller -r $LockedRequirements")
        assert install_idx >= 0, "missing the locked install"
        if_idx = text.find("if (-not $CoreOnly) {", install_idx)
        assert if_idx >= 0, "missing the full-bundle branch"
        sync_idx = text.find("sync_akms_claude_assets.py", if_idx)
        api_idx = text.find("& uv pip install --no-deps ./services/logic_loom_api")
        assert if_idx < sync_idx < api_idx, (
            "sync_akms_claude_assets.py must run inside the full-bundle branch, "
            "before logic-loom-api is installed"
        )

    def test_script_collects_akms_packages(self) -> None:
        """Verifies: PyInstaller collects every AKMS-family package."""
        text = PS_SCRIPT.read_text()
        for package in (
            "akms",
            "akms_nodes_gen",
            "akms_learn",
            "akms_failure_memory",
            "akms_records",
            "compmech_reference_pack",
        ):
            assert f'"--collect-all", "{package}"' in text, (
                f"windows script does not collect {package}"
            )

    def test_script_uses_stop_error_action(self) -> None:
        """Verifies: $ErrorActionPreference = Stop is set so failures abort the build.

        Requirement: matches the macOS/Linux `set -euo pipefail` invariant.
        """
        text = PS_SCRIPT.read_text()
        assert '$ErrorActionPreference = "Stop"' in text, (
            "Windows build must abort on first failure"
        )
