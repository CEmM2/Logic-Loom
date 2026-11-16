"""The release gates run clean on this tree.

`check_versions.py` is what the release workflow runs against the tag, and
`check_public_tree.py --security-only` is the scan every CI run performs. Both
are plain scripts, so they are exercised as a subprocess, the way CI runs them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=300
    )


def test_every_version_field_agrees() -> None:
    result = _run("scripts/check_versions.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_tag_that_does_not_match_is_refused() -> None:
    result = _run("scripts/check_versions.py", "--tag", "v999.0.0")
    assert result.returncode != 0


def test_tree_has_no_secrets_or_local_paths() -> None:
    result = _run("scripts/check_public_tree.py", "--security-only")
    assert result.returncode == 0, result.stdout + result.stderr


def test_full_audit_rejects_private_folders_references_and_paths(tmp_path: Path) -> None:
    (tmp_path / "dev").mkdir()
    (tmp_path / "dev" / "plan.md").write_text("notes\n")
    (tmp_path / "thirdparty").mkdir()
    (tmp_path / "thirdparty" / "x.txt").write_text("x\n")
    # The strings are split so this file itself stays clean under the audit.
    (tmp_path / "README.md").write_text(
        "See SOSO" "VSKI/Private-Thing and /Users/" "realperson/code/app.\n"
        "A placeholder like /home/" "user/project is fine.\n"
    )
    result = _run("scripts/check_public_tree.py", "--root", str(tmp_path))
    assert result.returncode == 1
    for expected in (
        "dev: forbidden top-level public path",
        "thirdparty: forbidden top-level public path",
        "README.md:1: private repository reference",
        "README.md:1: absolute macOS path",
    ):
        assert expected in result.stderr, result.stderr
    assert "README.md:2" not in result.stderr


def test_full_audit_passes_a_clean_tree(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Install from CEmM2/Logic-Loom-releases.\n")
    result = _run("scripts/check_public_tree.py", "--root", str(tmp_path))
    assert result.returncode == 0, result.stderr


def test_installers_ship_the_license_and_third_party_notices() -> None:
    import json

    conf = json.loads((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text())
    resources = conf["bundle"]["resources"]
    assert resources["binaries"] == "binaries"
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        assert resources[f"../../../{name}"] == name
        assert (ROOT / name).is_file()


def test_third_party_notices_record_the_lockfiles_they_came_from() -> None:
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "<!-- lockfile-sha256: uv.lock=" in text
    for section in ("## Python packages", "## JavaScript packages", "## Rust crates"):
        assert section in text
