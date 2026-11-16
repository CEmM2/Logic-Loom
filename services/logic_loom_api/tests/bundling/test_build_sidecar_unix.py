"""macOS/Linux sidecar build scripts.

These tests assert the static contract of the two shell scripts: the full
bundle is the default, every AKMS-family package is installed from uv.lock
rather than from a source checkout, and the AKMS asset sync runs inside the
full-bundle branch after those packages are installed. Build execution itself
is an integration smoke test that runs on the CI runner.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
MACOS_SCRIPT = ROOT / "scripts" / "build_sidecar_macos.sh"
LINUX_SCRIPT = ROOT / "scripts" / "build_sidecar_linux.sh"
SYNC_SCRIPT = ROOT / "scripts" / "sync_akms_claude_assets.py"


class TestBuildSidecarUnixScripts:
    """Static contract of the macOS and Linux sidecar build scripts."""

    @pytest.mark.parametrize("script_path", [MACOS_SCRIPT, LINUX_SCRIPT])
    def test_script_exists(self, script_path: Path) -> None:
        """Verifies: build scripts are present. Passes when: files exist."""
        assert script_path.exists(), f"{script_path} not found"

    @pytest.mark.parametrize("script_path", [MACOS_SCRIPT, LINUX_SCRIPT])
    def test_script_needs_no_source_checkout(self, script_path: Path) -> None:
        """Verifies: the build reads nothing from a vendored source tree.

        Every AKMS-family package is a published wheel, so a build that keys on
        `thirdparty/` would silently produce a core-only sidecar from a tree
        that does not carry it.
        """
        text = script_path.read_text()
        assert "thirdparty" not in text, (
            f"{script_path.name} must not depend on a vendored thirdparty/ tree"
        )

    @pytest.mark.parametrize("script_path", [MACOS_SCRIPT, LINUX_SCRIPT])
    def test_script_installs_from_the_lockfile(self, script_path: Path) -> None:
        """Verifies: dependencies come from `uv export --locked`, by extra name.

        A version range restated in a build script drifts from pyproject.toml
        (the scripts once pinned an AKMS minor the application no longer
        supported). Passes when: the export is locked, names the extras, and no
        AKMS requirement string appears in the script.
        """
        text = script_path.read_text()
        assert "uv export" in text and "--locked" in text
        assert "SIDECAR_EXTRAS=(akms mechdsl failure-memory records)" in text
        assert "akms[" not in text, (
            f"{script_path.name} restates an AKMS requirement; declare it in "
            "services/logic_loom_api/pyproject.toml instead"
        )

    @pytest.mark.parametrize("script_path", [MACOS_SCRIPT, LINUX_SCRIPT])
    def test_script_calls_sync_inside_full_bundle_branch(self, script_path: Path) -> None:
        """Verifies: the asset sync runs in the full-bundle branch, after the
        dependency install and before logic-loom-api is installed.

        The sync reads the installed `akms` package and writes into the
        resources tree that the logic-loom-api install then copies.
        """
        text = script_path.read_text()
        install_idx = text.find('uv pip install pyinstaller -r "$LOCKED_REQUIREMENTS"')
        assert install_idx >= 0, f"{script_path.name} missing the locked install"
        if_idx = text.find('if [[ "$CORE_ONLY" != "1" ]]; then', install_idx)
        assert if_idx >= 0, f"{script_path.name} missing the full-bundle branch"
        fi_idx = text.find("\nfi\n", if_idx)
        sync_idx = text.find("sync_akms_claude_assets.py", if_idx)
        api_idx = text.find("uv pip install --no-deps ./services/logic_loom_api")
        assert if_idx < sync_idx < fi_idx, (
            f"{script_path.name} must invoke sync_akms_claude_assets.py inside "
            "the full-bundle branch"
        )
        assert fi_idx < api_idx, (
            f"{script_path.name} must install logic-loom-api after the asset sync"
        )

    @pytest.mark.parametrize("script_path", [MACOS_SCRIPT, LINUX_SCRIPT])
    def test_script_collects_akms_packages(self, script_path: Path) -> None:
        """Verifies: PyInstaller collects every AKMS-family package.

        Requirement: the produced binary contains the AKMS tabs, Learn, the
        MechDSL bridge, Failure Memory and the Records browser.
        """
        text = script_path.read_text()
        for package in (
            "akms",
            "akms_nodes_gen",
            "akms_learn",
            "akms_failure_memory",
            "akms_records",
            "compmech_reference_pack",
        ):
            assert f"--collect-all {package}\n" in text, (
                f"{script_path.name} does not collect {package}"
            )

    def test_sync_script_present(self) -> None:
        """Verifies: the sync script the build scripts invoke exists."""
        assert SYNC_SCRIPT.exists(), "scripts/sync_akms_claude_assets.py missing"
