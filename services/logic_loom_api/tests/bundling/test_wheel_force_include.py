"""Force-include resources in the Python wheel.

Covers:
- A built wheel's contents include
  logic_loom_api/resources/akms_claude/.claude/skills/node-gen/SKILL.md.

The test exercises the pyproject.toml configuration in two complementary ways:
  1. **Static**: parse `services/logic_loom_api/pyproject.toml` and assert the
     force-include rule maps `src/logic_loom_api/resources` ->
     `logic_loom_api/resources` (and the existing templates rule is intact).
  2. **Dynamic (uv build)**: build the wheel into a tmp output dir, list its
     contents, and assert the canary SKILL.md path is present. The dynamic
     test is skipped if `uv` is not on PATH or if the resources tree has not
     yet been materialised by `scripts/sync_akms_claude_assets.py`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
PYPROJECT = REPO_ROOT / "services" / "logic_loom_api" / "pyproject.toml"
RESOURCES_CANARY = (
    REPO_ROOT
    / "services"
    / "logic_loom_api"
    / "src"
    / "logic_loom_api"
    / "resources"
    / "akms_claude"
    / ".claude"
    / "skills"
    / "node-gen"
    / "SKILL.md"
)
WHEEL_INTERNAL_CANARY = (
    "logic_loom_api/resources/akms_claude/.claude/skills/node-gen/SKILL.md"
)


def _force_include(data: dict) -> dict:
    return (
        data.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
        .get("force-include", {})
    )


class TestWheelForceInclude:
    """The wheel force-includes the bundled resources tree."""

    @pytest.mark.unit
    def test_pyproject_force_include_has_resources_entry(self):
        """Verifies: pyproject.toml carries the resources force-include rule.

        Requirement: the new mapping is declared. Trailing-slash sensitivity is mitigated
        by mirroring the existing templates entry shape (no trailing slash).
        """
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        fi = _force_include(data)
        assert fi, "force-include table missing entirely"
        assert (
            fi.get("src/logic_loom_api/resources") == "logic_loom_api/resources"
        ), (
            "expected force-include rule "
            "'src/logic_loom_api/resources' -> 'logic_loom_api/resources'; "
            f"got: {fi!r}"
        )
        # Preserved invariant — the existing templates rule must still be there.
        assert (
            fi.get("src/logic_loom_api/templates") == "logic_loom_api/templates"
        ), "templates force-include rule was removed or altered"

    @pytest.mark.unit
    def test_force_include_paths_are_relative_no_trailing_slash(self):
        """Verifies: rule paths have no trailing slash — hatchling-sensitive."""
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        fi = _force_include(data)
        for src, dst in fi.items():
            assert not src.endswith("/"), f"force-include source has trailing slash: {src!r}"
            assert not dst.endswith("/"), f"force-include dest has trailing slash: {dst!r}"

    @pytest.mark.integration
    def test_wheel_force_include_lists_resources(self, tmp_path):
        """Verifies: a built wheel contains the bundled AKMS Claude resources.

        Requirement: wheel contents include
        logic_loom_api/resources/akms_claude/.claude/skills/node-gen/SKILL.md.
        Skipped if `uv` is not available or if the resources tree has not
        been materialised yet (the sync script must run before wheel build).
        """
        uv_path = shutil.which("uv")
        if uv_path is None:
            pytest.skip("uv CLI not on PATH; skipping wheel-build integration test")
        if not RESOURCES_CANARY.exists():
            pytest.skip(
                "resources tree not materialised — run scripts/sync_akms_claude_assets.py "
                "before this test (the sidecar build scripts wire this in CI)"
            )

        out_dir = tmp_path / "wheel_out"
        out_dir.mkdir()
        env = os.environ.copy()
        # Keep wheel build self-contained and quiet.
        proc = subprocess.run(
            [
                uv_path,
                "build",
                "--wheel",
                "--package",
                "logic-loom-api",
                "-o",
                str(out_dir),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"uv build failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

        wheels = list(out_dir.glob("logic_loom_api-*.whl"))
        assert wheels, f"no wheel produced under {out_dir}; build stdout: {proc.stdout}"

        with zipfile.ZipFile(wheels[0]) as zf:
            names = zf.namelist()
        assert WHEEL_INTERNAL_CANARY in names, (
            f"expected {WHEEL_INTERNAL_CANARY!r} in wheel contents; "
            f"got {len(names)} entries, sample: {names[:5]}"
        )
