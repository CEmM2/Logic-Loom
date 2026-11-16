"""Vendor-refresh regression: scripts/sync_akms_claude_assets.py must be able
to express the `hooks` and `docs` section groups.

Context: the Logic-Loom -> AKMS vendor refresh (bumping the vendored tree to
AKMS commit f32d81f) extended `packaging/akms_claude_assets.toml` with new
sections sourced from AKMS's *published root* paths (`skills/akms`,
`skills/deepwiki-nodes`, `skills/akms-spec-check`, `hooks/`,
`docs/akms/spec/invariants.md`) rather than `.claude/`, because `.claude/` is
stripped from every published AKMS copy. Before this refresh,
`SECTION_GROUPS` in the sync script only iterated
`("skills", "agents", "commands", "root_scripts", "prompt_library_defaults")`
— a manifest section under `[hooks.*]` or `[docs.*]` would have silently been
skipped (`data.get(group_name, {})` returns `{}` for an unlisted group, so
`main()` would report success without ever copying those files). This test
guards that regression: it builds a minimal fixture manifest with `hooks` and
`docs` sections and asserts the sync script actually copies their declared
files, using the same fixture-module-loading approach as
`test_sync_claude_assets_script.TestSyncClaudeAssetsScript`.
"""
from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_akms_claude_assets.py"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location(
        "sync_akms_claude_assets_hooks_docs_under_test", SYNC_SCRIPT
    )
    assert spec and spec.loader, "could not load sync script as a module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_manifest(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


class TestSyncScriptSupportsHooksAndDocsGroups:
    """Regression coverage for the `hooks`/`docs` SECTION_GROUPS extension."""

    @pytest.mark.unit
    def test_hooks_and_docs_are_declared_section_groups(self):
        """Verifies: `hooks` and `docs` are in the script's SECTION_GROUPS.

        Passes when: both names appear in the tuple the script iterates in
        `main()`. Fails loudly (not silently) if either is missing, since a
        manifest entry under an unlisted group is a silent no-op today.
        """
        mod = _load_sync_module()
        assert "hooks" in mod.SECTION_GROUPS
        assert "docs" in mod.SECTION_GROUPS

    @pytest.mark.integration
    def test_hooks_and_docs_sections_are_actually_copied(self, tmp_path, monkeypatch):
        """Verifies: a manifest entry under [hooks.*] / [docs.*] is copied.

        Requirement: builds a fixture tree with one hooks file and one docs file,
        declares them under `[hooks.guards]` / `[docs.akms_spec]`, runs the
        real `main()` against the fixture root, and asserts both files landed
        at their declared targets. This is the behavior the pre-refresh
        script could not express (the groups were not iterated at all).
        """
        mod = _load_sync_module()
        monkeypatch.setattr(mod, "ROOT", tmp_path)
        monkeypatch.setattr(mod, "MANIFEST", tmp_path / "packaging" / "akms_claude_assets.toml")
        resources_root = tmp_path / "resources_out" / "akms_claude"
        monkeypatch.setattr(mod, "RESOURCES_ROOT", resources_root)
        # Point the canary at a file this fixture actually provides so the
        # canary guard doesn't fail on unrelated missing sections.
        monkeypatch.setattr(mod, "CANARY_REL", Path("hooks") / "akms-guard-global-vault.sh")

        akms = tmp_path / "AKMS_fixture"
        (akms / "hooks").mkdir(parents=True)
        (akms / "docs" / "akms" / "spec").mkdir(parents=True)
        (akms / "hooks" / "akms-guard-global-vault.sh").write_text("#!/usr/bin/env bash\n")
        (akms / "docs" / "akms" / "spec" / "invariants.md").write_text("# Invariants\n")

        _write_manifest(
            mod.MANIFEST,
            """
            [hooks.guards]
            source = "AKMS_fixture/hooks"
            target = "resources_out/akms_claude/hooks"
            files = ["akms-guard-global-vault.sh"]

            [docs.akms_spec]
            source = "AKMS_fixture/docs/akms/spec"
            target = "resources_out/akms_claude/docs/akms/spec"
            files = ["invariants.md"]
            """,
        )

        rc = mod.main()
        assert rc == 0

        hooks_copy = resources_root / "hooks" / "akms-guard-global-vault.sh"
        docs_copy = resources_root / "docs" / "akms" / "spec" / "invariants.md"
        assert hooks_copy.exists(), "hooks/* section was not copied — SECTION_GROUPS regression"
        assert docs_copy.exists(), "docs/* section was not copied — SECTION_GROUPS regression"
        assert hooks_copy.read_text(encoding="utf-8") == "#!/usr/bin/env bash\n"
        assert docs_copy.read_text(encoding="utf-8") == "# Invariants\n"
