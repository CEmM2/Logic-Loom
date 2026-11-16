"""Runtime AKMS path references resolve via akms_workspace, not akms_repo.

Covers:
1. All identified runtime-read paths resolve via Settings.akms_workspace.
2. The picker still mounts in dev with a sibling AKMS repo (verified via the
   _build_paths unit test below — the integration mount test lives in
   ``test_picker_mount.py`` and requires the AKMS sibling repo + extra, so
   it skips in this environment).

This is regression-tier coverage — the heavyweight assertions live in the
existing backend suite (``pytest services/logic_loom_api/tests -q``); this
file adds focused unit coverage for the migration itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from logic_loom_api.adapters.picker_adapter import _build_paths
from logic_loom_api.config import Settings


@dataclass
class FakePaths:
    """A stand-in for ``akms_nodes_gen.batch_picker.config.Paths`` so this
    test does not require the AKMS extra to be installed."""

    workspace: Path
    plan_md: Path
    bbt_json: Path
    zotsums_root: Path
    state_file: Path
    queries_file: Path
    inputs_dir: Path
    sources_dir: Path


class TestPickerAdapterMigration:
    """Runtime-read paths must come from akms_workspace, not akms_repo."""

    def test_runtime_reads_resolve_via_workspace(self, tmp_path):
        """state_file / queries_file / inputs_dir / sources_dir all live
        under akms_workspace (not akms_repo) after migration.
        """
        akms_repo = tmp_path / "AKMS"
        workspace = tmp_path / "WS"
        vault = tmp_path / "vault"
        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_repo=akms_repo,
            akms_workspace=workspace,
            vault_path=vault,
        )
        paths = _build_paths(s, FakePaths)

        # Runtime-read paths now anchor on workspace.
        assert paths.state_file == workspace / "batch_assignments.json"
        assert paths.queries_file == workspace / "saved_queries.json"
        assert paths.inputs_dir == workspace / "Sources_Evals" / "NLM" / "Inputs"
        assert paths.sources_dir == workspace / "AKMS_Sources" / "new"

        # Sanity: none of the runtime-read paths leak akms_repo.
        for p in (paths.state_file, paths.queries_file, paths.inputs_dir, paths.sources_dir):
            assert str(akms_repo) not in str(p), f"{p} should not reference akms_repo"

    def test_dev_time_sources_still_anchor_on_akms_repo(self, tmp_path):
        """plan_md (a dev-time source) stays under akms_repo when that
        checkout is present (it is an optional developer override), while
        ``workspace`` is always the Logic-Loom workspace."""
        akms_repo = tmp_path / "AKMS"
        akms_repo.mkdir()
        workspace = tmp_path / "WS"
        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_repo=akms_repo,
            akms_workspace=workspace,
            vault_path=tmp_path / "vault",
        )
        paths = _build_paths(s, FakePaths)

        assert paths.workspace == workspace
        assert paths.plan_md == akms_repo / "Packages" / "AKMS_nodes_gen" / "generation_plan.md"

    def test_vault_paths_resolve_when_vault_set(self, tmp_path):
        """When vault_path is set, zotsums_root + bbt_json wire to the vault."""
        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_repo=tmp_path / "AKMS",
            akms_workspace=tmp_path / "WS",
            vault_path=tmp_path / "vault",
        )
        paths = _build_paths(s, FakePaths)
        assert paths.zotsums_root == (tmp_path / "vault")
        assert paths.bbt_json == (tmp_path / "vault" / "zsumbib.json")

    def test_no_akms_checkout_anchors_on_workspace(self, tmp_path):
        """With no akms_repo checkout on disk, workspace and the default
        plan_md resolve inside the workspace — never under the missing repo."""
        akms_repo = tmp_path / "no-akms-source-checkout"
        workspace = tmp_path / "WS"
        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_repo=akms_repo,
            akms_workspace=workspace,
            vault_path=tmp_path / "vault",
        )
        paths = _build_paths(s, FakePaths)
        assert paths.workspace == workspace
        assert paths.plan_md == workspace / ".cache" / "generation_plan.md"
        for p in vars(paths).values():
            assert str(akms_repo) not in str(p)

    def test_catalog_from_akms_env_when_vault_unset(self, tmp_path, monkeypatch):
        """No vault_path: AKMS's own env overrides locate the catalog."""
        monkeypatch.delenv("LOGIC_LOOM_VAULT_PATH", raising=False)
        monkeypatch.delenv("AKMS_BBT_JSON", raising=False)
        monkeypatch.setenv("AKMS_ZOTSUMS_ROOT", str(tmp_path / "zs"))
        s = Settings.load(
            config_path=tmp_path / "missing.toml", akms_workspace=tmp_path / "WS"
        )
        assert s.vault_path is None
        paths = _build_paths(s, FakePaths)
        assert paths.zotsums_root == tmp_path / "zs"
        assert paths.bbt_json == tmp_path / "zs" / "zsumbib.json"

        monkeypatch.delenv("AKMS_ZOTSUMS_ROOT")
        monkeypatch.setenv("AKMS_BBT_JSON", str(tmp_path / "bbt" / "lib.json"))
        paths = _build_paths(s, FakePaths)
        assert paths.zotsums_root == tmp_path / "bbt"
        assert paths.bbt_json == tmp_path / "bbt" / "lib.json"

    def test_no_catalog_source_uses_zsum_default_not_author_default(
        self, tmp_path, monkeypatch
    ):
        """No vault_path and no AKMS env → zsum's default ``~/ZotSums``, never
        the vendored 0.1.0 hard-coded author-machine OneDrive default."""
        for var in ("LOGIC_LOOM_VAULT_PATH", "AKMS_BBT_JSON", "AKMS_ZOTSUMS_ROOT"):
            monkeypatch.delenv(var, raising=False)
        s = Settings.load(
            config_path=tmp_path / "missing.toml", akms_workspace=tmp_path / "WS"
        )
        paths = _build_paths(s, FakePaths)
        zotsums = Path("~/ZotSums").expanduser()
        assert paths.zotsums_root == zotsums
        assert paths.bbt_json == zotsums / "zsumbib.json"
        assert "OneDrive" not in str(paths.bbt_json)



class TestPreservedInvariants:
    """Invariants the akms_workspace migration must NOT break."""

    def test_effective_akms_global_vault_layered_fallback_intact(self, tmp_path, monkeypatch):
        """The graph source vault resolver still uses its layered fallback
        (explicit → akms_repo/Packages/Nodes_Vault → AKMS_GLOBAL_VAULT env →
        ~/.claude/akms/nodes default).

        effective_akms_global_vault() must stay intact — it already has a
        layered fallback.
        """
        # Layer 1: explicit override wins.
        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_global_vault=tmp_path / "explicit",
        )
        assert s.effective_akms_global_vault() == (tmp_path / "explicit")

        # Layer 2: akms_repo/Packages/Nodes_Vault wins when no explicit and it exists.
        repo = tmp_path / "AKMS"
        nodes_vault = repo / "Packages" / "Nodes_Vault"
        nodes_vault.mkdir(parents=True)
        s2 = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_repo=repo,
        )
        assert s2.effective_akms_global_vault() == nodes_vault

        # Layer 3: AKMS_GLOBAL_VAULT env wins when explicit unset and repo path absent.
        env_path = tmp_path / "env_vault"
        monkeypatch.setenv("AKMS_GLOBAL_VAULT", str(env_path))
        s3 = Settings.load(
            config_path=tmp_path / "missing.toml",
            akms_repo=tmp_path / "no-akms",
        )
        assert s3.effective_akms_global_vault() == env_path

    def test_graph_cache_path_stays_under_cache_path_not_workspace(self, tmp_path):
        """Graph cache lives under cache_path (not akms_workspace) — invariant.

        The compiled graph cache must continue to land under cache_path, not
        akms_workspace.
        """
        cache = tmp_path / "cache"
        ws = tmp_path / "WS"
        s = Settings.load(
            config_path=tmp_path / "missing.toml",
            cache_path=cache,
            akms_workspace=ws,
        )
        assert s.graph_cache_path() == cache / "graph" / "graph.json"
        assert str(ws) not in str(s.graph_cache_path())
