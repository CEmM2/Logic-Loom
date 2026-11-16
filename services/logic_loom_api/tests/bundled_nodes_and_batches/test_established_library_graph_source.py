"""Tests for merging the shipped library as a read-only second vault source.

Covers:
    1 (with no user vault, established nodes still appear),
    2 (with a user vault, both user and established nodes appear),
    3 (neither source is mutated by compile — read-only graph invariant),
    4 (cache invalidates on a shipped-library change).

The graph compile entry is ``adapters/graph_adapter.scan_graph_sources`` →
``_graph_source_specs(repo_root, global_vault)`` (source kinds global-vault /
local-nodes / code-mirror). The shipped library adds the in-app
``resources/akms_nodes/established/`` as an ADDITIONAL read-only source and
keys ``graph_source_key`` / ``graph_cache_is_stale`` on both roots.

NOTE: the shipped tree ``resources/akms_nodes/established/`` is a GITIGNORED
build artifact — run ``scripts/sync_akms_nodes.py`` (or point at
``verified_nodes/`` / a tmp fixture) before asserting it exists. These tests
need the AKMS extra (``pytest.importorskip("akms")``) — the graph compiler.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("akms")  # graph compiler — established → graph needs AKMS

import frontmatter  # type: ignore[import-not-found,import-untyped]  # noqa: E402
import yaml  # type: ignore[import-untyped]  # noqa: E402

from logic_loom_api.adapters import graph_adapter as ga  # noqa: E402
from logic_loom_api.config import Settings  # noqa: E402


# ── Fixture helpers ──────────────────────────────────────────────────────

_ESTABLISHED_NODE_IDS = {
    "newton-raphson",
    "finite-differences-basics",
    "j2-plasticity",
    "elastic-isotropic",
    "continuum-kinematics",
}


def _repo_root() -> Path:
    """Repository root (…/Logic-Loom), four parents up from this test file."""
    return Path(__file__).resolve().parents[4]


def _ensure_established_library() -> Path:
    """Return the shipped established library root, running the sync if absent.

    The tree is a gitignored build artifact; on a fresh checkout it does not
    exist. We run ``scripts/sync_akms_nodes.py`` once to materialise it (from
    the tracked ``verified_nodes/``). If the sync cannot run (e.g. no source),
    the test is skipped rather than failed — matching the degrade-gracefully
    invariant.
    """
    root = ga.established_library_root()
    if root is not None and any(root.glob("**/*.md")):
        return root

    repo = _repo_root()
    sync_script = repo / "scripts" / "sync_akms_nodes.py"
    if not sync_script.is_file():
        pytest.skip("sync_akms_nodes.py not present; cannot materialise library")
    try:
        subprocess.run(
            [sys.executable, str(sync_script)],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError) as e:  # pragma: no cover
        pytest.skip(f"established library sync unavailable: {e}")

    root = ga.established_library_root()
    if root is None or not any(root.glob("**/*.md")):
        pytest.skip("established library still empty after sync")
    return root


def _make_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build an empty AKMS repo scaffold + user vault + cache under tmp_path."""
    repo = tmp_path / "AKMS"
    vault = tmp_path / "vault"
    cache = tmp_path / "cache"
    (repo / "knowledge" / "graph").mkdir(parents=True)
    (repo / "knowledge" / "local-nodes").mkdir(parents=True)
    (repo / "knowledge" / "code-mirror").mkdir(parents=True)
    vault.mkdir(parents=True)
    (repo / "knowledge" / "graph" / "local_state.yaml").write_text(
        yaml.dump(
            {
                "akms_schema": "v2",
                "repo_id": "test-repo",
                "nodes": {},
                "local_edges": [],
                "session_nodes": {},
                "suppressed_edges": [],
            }
        )
    )
    return repo, vault, cache


def _write_user_node(vault: Path, node_id: str) -> None:
    post = frontmatter.Post(f"# {node_id}\n")
    post.metadata = {
        "id": node_id,
        "title": node_id.replace("-", " ").title(),
        "domain": "user-domain",
        "tags": ["user"],
        "status": "established",
        "confidence": 0.9,
        "source": "human",
        "edges": [],
        "akms_schema": "v2",
    }
    with (vault / f"{node_id}.md").open("wb") as f:
        f.write(frontmatter.dumps(post).encode("utf-8"))


def _settings(repo: Path, vault: Path, cache: Path) -> Settings:
    return Settings(
        akms_repo=repo,
        akms_global_vault=vault,
        cache_path=cache,
    )


def _source_mtimes(root: Path) -> dict[str, int]:
    return {str(p): p.stat().st_mtime_ns for p in sorted(root.glob("**/*.md"))}


# ── Tests ────────────────────────────────────────────────────────────────


class TestEstablishedLibraryGraphSource:
    """Integration tests for the dual-source graph compile."""

    @pytest.mark.integration
    def test_established_only_source_compiles(self, tmp_path):
        """Verifies: with NO user vault, the shipped established library still
        compiles into the graph (its nodes appear)."""
        _ensure_established_library()
        repo, vault, cache = _make_repo(tmp_path)
        # User vault is present-but-empty → no user nodes; established is the
        # ONLY source of nodes.
        settings = _settings(repo, vault, cache)

        cg = ga.build_cached_graph(settings, skip_invalid_files=True)
        ids = set(cg.graph.nodes)

        assert _ESTABLISHED_NODE_IDS.issubset(ids), (
            f"established nodes missing from established-only compile: "
            f"{_ESTABLISHED_NODE_IDS - ids}"
        )
        # every compiled node is an established node (no user nodes seeded)
        assert ids == _ESTABLISHED_NODE_IDS

    @pytest.mark.integration
    def test_user_vault_only_compiles(self, tmp_path, monkeypatch):
        """Verifies: with only a user vault (no established library on the
        resolved path), the user's nodes still compile (baseline)."""
        # Force the established library to be ABSENT — proves graceful degrade
        # and that the user vault alone still compiles.
        monkeypatch.setattr(ga, "established_library_root", lambda: None)
        repo, vault, cache = _make_repo(tmp_path)
        _write_user_node(vault, "user-node-a")
        _write_user_node(vault, "user-node-b")
        settings = _settings(repo, vault, cache)

        cg = ga.build_cached_graph(settings, skip_invalid_files=True)
        ids = set(cg.graph.nodes)

        assert ids == {"user-node-a", "user-node-b"}
        assert ids.isdisjoint(_ESTABLISHED_NODE_IDS)

    @pytest.mark.integration
    def test_both_sources_union_read_only(self, tmp_path):
        """Verifies: with both a user vault and the shipped library, the graph
        is the UNION of both, and NEITHER source file is mutated by compile
        (read-only graph invariant)."""
        established_root = _ensure_established_library()
        repo, vault, cache = _make_repo(tmp_path)
        _write_user_node(vault, "user-node-a")
        settings = _settings(repo, vault, cache)

        # Capture source fingerprints BEFORE compile.
        est_before = _source_mtimes(established_root)
        vault_before = _source_mtimes(vault)

        cg = ga.build_cached_graph(settings, skip_invalid_files=True)
        ids = set(cg.graph.nodes)

        # Union: every established node + the user node.
        assert _ESTABLISHED_NODE_IDS.issubset(ids)
        assert "user-node-a" in ids
        assert ids == _ESTABLISHED_NODE_IDS | {"user-node-a"}

        # Read-only invariant: neither source mutated (no new files, no mtime
        # changes, no deletions).
        assert _source_mtimes(established_root) == est_before, "established source mutated by compile"
        assert _source_mtimes(vault) == vault_before, "user vault mutated by compile"

    @pytest.mark.integration
    def test_cache_invalidates_on_library_change(self, tmp_path, monkeypatch):
        """Verifies: changing the shipped library changes the graph cache key
        / invalidates the cache so the update is reflected."""
        established_root = _ensure_established_library()
        repo, vault, cache = _make_repo(tmp_path)
        _write_user_node(vault, "user-node-a")

        # Copy the real established library into a WRITABLE tmp mirror so we can
        # mutate it without touching the shipped (read-only) source, then point
        # the resolver at the mirror.
        import shutil

        est_mirror = tmp_path / "established-mirror"
        shutil.copytree(established_root, est_mirror)
        monkeypatch.setattr(ga, "established_library_root", lambda: est_mirror)

        settings = _settings(repo, vault, cache)

        # Build once, record the source key stamped into the cache.
        cg1 = ga.build_cached_graph(settings, skip_invalid_files=True)
        assert _ESTABLISHED_NODE_IDS.issubset(set(cg1.graph.nodes))
        key1 = ga.graph_source_key(settings)
        assert ga.graph_cache_is_stale(settings) is False

        # Mutate the library: add a NEW established node.
        new_node = est_mirror / "numerical-methods" / "p4-1-probe.md"
        new_node.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post("# probe\n")
        post.metadata = {
            "id": "p4-1-probe",
            "title": "Library Change Probe",
            "domain": "numerical-methods",
            "tags": ["probe"],
            "status": "established",
            "confidence": 0.9,
            "source": "human",
            "edges": [],
            "akms_schema": "v2",
        }
        with new_node.open("wb") as f:
            f.write(frontmatter.dumps(post).encode("utf-8"))

        # The source key must change and the cache must now be stale.
        key2 = ga.graph_source_key(settings)
        assert key2 != key1, "library change did not change the source key"
        assert ga.graph_cache_is_stale(settings) is True

        # A rebuild reflects the change: the new node is present.
        cg2 = ga.build_cached_graph(settings, skip_invalid_files=True)
        assert "p4-1-probe" in set(cg2.graph.nodes)
        assert ga.graph_cache_is_stale(settings) is False
