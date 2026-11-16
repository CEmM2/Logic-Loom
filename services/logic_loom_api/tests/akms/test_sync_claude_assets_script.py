"""scripts/sync_akms_claude_assets.py.

Covers:
- Running the script with ../AKMS/ present produces a populated
  resources/akms_claude/.claude/skills/node-gen/SKILL.md.
- Missing source -> SystemExit (loud failure).
- Final canary guard raises if no SKILL.md exists under
  .claude/skills/node-gen/ in the materialized tree.
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_akms_claude_assets.py"


def _load_sync_module():
    """Import the sync script as a module (its filename is not a valid
    Python module identifier because of the hyphen-free underscore style)."""
    spec = importlib.util.spec_from_file_location(
        "sync_akms_claude_assets_under_test", SYNC_SCRIPT
    )
    assert spec and spec.loader, "could not load sync script as a module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_manifest(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def _make_fixture_layout(root: Path) -> dict:
    """Build a minimal AKMS-shaped source layout under `root`.

    Returns the manifest dict that addresses these files via paths relative
    to the same `root` (the script uses ROOT-relative paths internally;
    tests patch ROOT to point at this fixture root).
    """
    akms = root / "AKMS_fixture" / ".claude"
    (akms / "skills" / "node-gen" / "references").mkdir(parents=True)
    (akms / "skills" / "node-gen" / "scripts").mkdir(parents=True)
    (akms / "agents").mkdir(parents=True)
    (akms / "commands").mkdir(parents=True)

    (akms / "skills" / "node-gen" / "SKILL.md").write_text("# Node-gen SKILL\n")
    (akms / "skills" / "node-gen" / "references" / "schema_compliance.md").write_text("schema\n")
    (akms / "skills" / "node-gen" / "scripts" / "akms_node_convert.py").write_text("# convert\n")
    (akms / "agents" / "digest-refiner.md").write_text("# digest\n")
    (akms / "commands" / "node-gen-invoker.md").write_text("# invoker\n")

    target = "resources_out/akms_claude"
    return {
        "skills": {
            "node_gen": {
                "source": "AKMS_fixture/.claude/skills/node-gen",
                "target": f"{target}/.claude/skills/node-gen",
                "files": ["SKILL.md", "references/schema_compliance.md", "scripts/akms_node_convert.py"],
            }
        },
        "agents": {
            "digest_refiner": {
                "source": "AKMS_fixture/.claude/agents",
                "target": f"{target}/.claude/agents",
                "files": ["digest-refiner.md"],
            }
        },
        "commands": {
            "node_gen_invoker": {
                "source": "AKMS_fixture/.claude/commands",
                "target": f"{target}/.claude/commands",
                "files": ["node-gen-invoker.md"],
            }
        },
        "root_scripts": {
            "node_gen_scripts": {
                "source": "AKMS_fixture/.claude/skills/node-gen/scripts",
                "target": f"{target}/scripts",
                "files": ["akms_node_convert.py"],
            }
        },
    }


def _manifest_to_toml(data: dict) -> str:
    """Tiny TOML emitter sufficient for the fixture schema (no nesting beyond
    `[group.name]` tables with string + list-of-strings keys)."""
    out: list[str] = []
    for group, sections in data.items():
        for name, section in sections.items():
            out.append(f"[{group}.{name}]")
            out.append(f'source = "{section["source"]}"')
            out.append(f'target = "{section["target"]}"')
            files = ", ".join(f'"{f}"' for f in section["files"])
            out.append(f"files = [{files}]")
            out.append("")
    return "\n".join(out)


@pytest.fixture
def patched_sync(tmp_path, monkeypatch):
    """Provide an isolated copy of the sync module with ROOT/MANIFEST/
    RESOURCES_ROOT pointed at a tmp fixture tree."""
    mod = _load_sync_module()
    # Build fixture root.
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "MANIFEST", tmp_path / "packaging" / "akms_claude_assets.toml")
    monkeypatch.setattr(
        mod,
        "RESOURCES_ROOT",
        tmp_path / "resources_out" / "akms_claude",
    )
    return mod, tmp_path


class TestSyncClaudeAssetsScript:
    """Tests for scripts/sync_akms_claude_assets.py."""

    @pytest.mark.integration
    def test_sync_against_fixture_akms_layout(self, patched_sync):
        """Verifies: sync copies each declared file under a fixture AKMS layout.

        Requirement: bundled resources tree is fully populated. Passes when every file
        declared in a tmp-path manifest exists at the target after a sync run
        and contents equal the source.
        """
        mod, root = patched_sync
        manifest = _make_fixture_layout(root)
        _write_manifest(mod.MANIFEST, _manifest_to_toml(manifest))

        rc = mod.main()
        assert rc == 0

        # Canary file landed.
        canary = mod.RESOURCES_ROOT / ".claude" / "skills" / "node-gen" / "SKILL.md"
        assert canary.exists()
        assert canary.read_text(encoding="utf-8") == "# Node-gen SKILL\n"

        # All declared files copied across all sections.
        for group, sections in manifest.items():
            for _name, section in sections.items():
                for rel in section["files"]:
                    src = root / section["source"] / rel
                    dst = root / section["target"] / rel
                    assert dst.exists(), f"missing copy of {rel} under {dst}"
                    assert dst.read_bytes() == src.read_bytes(), (
                        f"content mismatch for {rel}"
                    )

    @pytest.mark.integration
    def test_sync_fails_on_missing_source(self, patched_sync):
        """Verifies: SystemExit is raised when a declared source file is absent.

        Requirement: loud failure — `SystemExit` (or non-zero exit) when a manifest
        entry's source file is missing. No silent skip; no fallback to globs.
        """
        mod, root = patched_sync
        manifest = _make_fixture_layout(root)
        # Inject a missing file into the skills section.
        manifest["skills"]["node_gen"]["files"].append("references/does_not_exist.md")
        _write_manifest(mod.MANIFEST, _manifest_to_toml(manifest))

        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert "Missing declared AKMS Claude asset" in str(exc.value)

    @pytest.mark.integration
    def test_sync_fails_on_missing_source_directory(self, patched_sync):
        """Verifies: SystemExit when an entire section.source directory is absent."""
        mod, root = patched_sync
        manifest = _make_fixture_layout(root)
        manifest["skills"]["node_gen"]["source"] = "AKMS_fixture/.claude/skills/does-not-exist"
        _write_manifest(mod.MANIFEST, _manifest_to_toml(manifest))

        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert "Missing source directory" in str(exc.value)

    @pytest.mark.integration
    def test_sync_guard_fails_on_missing_skill_md(self, patched_sync):
        """Verifies: final guard raises if no SKILL.md exists under .claude/skills/node-gen/.

        Requirement: the canary-file assertion fires when the resulting target_root has
        no SKILL.md under .claude/skills/node-gen/.
        """
        mod, root = patched_sync
        manifest = _make_fixture_layout(root)
        # Drop the SKILL.md so the canary will not exist.
        manifest["skills"]["node_gen"]["files"] = [
            "references/schema_compliance.md",
            "scripts/akms_node_convert.py",
        ]
        _write_manifest(mod.MANIFEST, _manifest_to_toml(manifest))

        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert "canary missing" in str(exc.value)

    @pytest.mark.integration
    def test_sync_prints_summary(self, patched_sync, capsys):
        """Verifies: one-line summary printed at end (count of files + sections)."""
        mod, root = patched_sync
        manifest = _make_fixture_layout(root)
        _write_manifest(mod.MANIFEST, _manifest_to_toml(manifest))

        mod.main()
        captured = capsys.readouterr().out
        assert "[akms-assets] sync complete:" in captured
        # 3 + 1 + 1 + 1 = 6 files across 4 sections.
        assert "6 files across 4 sections" in captured
