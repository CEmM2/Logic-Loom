"""packaging/akms_claude_assets.toml manifest.

Covers:
- TOML parses cleanly.
- Every listed source file exists in the installed `akms` package (or in-tree).
- Manifest contains the four required section groups with source/target/files.
"""
from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

# Repo root resolves from this test file path:
#   services/logic_loom_api/tests/akms/test_claude_assets_manifest.py
# -> parents[4] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
MANIFEST_PATH = REPO_ROOT / "packaging" / "akms_claude_assets.toml"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_akms_claude_assets.py"


def _sync_module():
    """The sync script as a module, for its `resolve_source_root`."""
    spec = importlib.util.spec_from_file_location("sync_akms_claude_assets_manifest", SYNC_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Required section groups of the manifest.
# The optional agents.akms_spec_reviewer entry is checked separately.
REQUIRED_GROUPS = ("skills", "agents", "commands", "root_scripts")
REQUIRED_SECTIONS = {
    "skills": ("node_gen",),
    "agents": ("digest_refiner",),
    "commands": ("node_gen_invoker",),
    "root_scripts": ("node_gen_scripts",),
}


class TestClaudeAssetsManifest:
    """Tests for the packaging/akms_claude_assets.toml manifest."""

    @pytest.mark.unit
    def test_manifest_parses(self):
        """Verifies: manifest is valid TOML.

        Passes when: tomllib.loads succeeds on the manifest file contents.
        """
        assert MANIFEST_PATH.exists(), f"manifest missing at {MANIFEST_PATH}"
        data = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert data, "manifest parsed to empty dict"

    @pytest.mark.unit
    def test_manifest_has_required_sections(self):
        """Verifies: manifest contains the four required section groups.

        Requirement: the expected structure (skills.node_gen, agents.digest_refiner,
        commands.node_gen_invoker, root_scripts.node_gen_scripts).
        Passes when: all four sections present and each has source/target/files.
        """
        data = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for group in REQUIRED_GROUPS:
            assert group in data, f"missing top-level group [{group}]"
            for name in REQUIRED_SECTIONS[group]:
                assert name in data[group], (
                    f"missing required section [{group}.{name}]"
                )
                section = data[group][name]
                assert isinstance(section, dict), f"{group}.{name} not a table"
                for key in ("source", "target", "files"):
                    assert key in section, (
                        f"[{group}.{name}] missing required key '{key}'"
                    )
                assert isinstance(section["files"], list), (
                    f"[{group}.{name}].files must be a list"
                )
                assert section["files"], (
                    f"[{group}.{name}].files is empty — whitelist must be explicit"
                )
                # No globs — security invariant.
                for entry in section["files"]:
                    assert isinstance(entry, str)
                    assert "*" not in entry and "?" not in entry, (
                        f"glob char in [{group}.{name}].files entry {entry!r}"
                    )

    @pytest.mark.unit
    def test_manifest_source_files_exist_when_akms_installed(self):
        """Verifies: every declared source file exists where the manifest says.

        The AKMS agent assets are read from the installed `akms` package, so
        this is skipped in an environment without the `akms` extra.
        """
        if importlib.util.find_spec("akms") is None:
            pytest.skip("the akms extra is not installed")

        sync = _sync_module()
        data = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        missing: list[str] = []
        for group in sync.SECTION_GROUPS:
            for name, section in data.get(group, {}).items():
                source_root = sync.resolve_source_root(section["source"], f"{group}.{name}")
                assert source_root.exists(), (
                    f"[{group}.{name}].source missing: {source_root}"
                )
                for rel in section["files"]:
                    src = source_root / rel
                    if not src.exists():
                        missing.append(f"[{group}.{name}] {src}")
        assert not missing, "Declared sources missing:\n  " + "\n  ".join(missing)

    @pytest.mark.unit
    def test_akms_assets_come_from_the_installed_package(self):
        """Verifies: no AKMS asset section reads a vendored source checkout.

        The assets must match the AKMS version the sidecar is built against,
        which only the installed package guarantees. In-tree sources are
        allowed (prompt-library defaults, the conformance page).
        """
        data = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for group in ("skills", "agents", "commands", "root_scripts", "hooks"):
            for name, section in data.get(group, {}).items():
                assert section["source"].startswith("pkg:akms/"), (
                    f"[{group}.{name}].source must be read from the akms package, "
                    f"got {section['source']!r}"
                )
        for group, sections in data.items():
            for name, section in sections.items():
                assert "thirdparty" not in section["source"], (
                    f"[{group}.{name}].source points into thirdparty/"
                )

    @pytest.mark.unit
    def test_akms_spec_reviewer_declared(self):
        """Verifies: [agents.akms_spec_reviewer] declares akms-spec-reviewer.md."""
        data = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = data["agents"]["akms_spec_reviewer"]
        assert "akms-spec-reviewer.md" in entry["files"]
