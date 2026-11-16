"""Regression tests: the graph cache key includes PROVENANCE.toml.

Previously ``graph_source_key`` fingerprinted only
``*.md`` files, but ``build_cached_graph`` stamps ``verified_by``/
``verified_at``/``version``/``source_ref`` from the established library's
``PROVENANCE.toml`` into ``graph.json``. So a release that updates ONLY the
manifest (e.g. a re-verification / version bump — no node body change) left the
source key unchanged: ``auto_build`` treated the cache as fresh and served
stale provenance until a manual rebuild.

This mirrors ``test_cache_invalidates_on_library_change`` in
test_established_library_graph_source.py but mutates
ONLY ``PROVENANCE.toml`` and asserts the key flips + the cache goes stale.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("akms")  # graph compiler — established → graph needs AKMS

import frontmatter  # type: ignore[import-not-found,import-untyped]  # noqa: E402
import yaml  # type: ignore[import-untyped]  # noqa: E402

from logic_loom_api.adapters import graph_adapter as ga  # noqa: E402
from logic_loom_api.config import Settings  # noqa: E402


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _ensure_established_library() -> Path:
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


def _settings(repo: Path, vault: Path, cache: Path) -> Settings:
    return Settings(akms_repo=repo, akms_global_vault=vault, cache_path=cache)


class TestGraphCacheKeyProvenance:
    """Cache-key regression: PROVENANCE.toml is a cache input."""

    @pytest.mark.integration
    def test_cache_invalidates_on_provenance_only_change(self, tmp_path, monkeypatch):
        """Mutating ONLY PROVENANCE.toml flips graph_source_key and marks the
        cache stale — provenance-only releases are no longer served stale."""
        established_root = _ensure_established_library()

        # Writable mirror so we can mutate the manifest without touching the
        # shipped (read-only) source.
        est_mirror = tmp_path / "established-mirror"
        shutil.copytree(established_root, est_mirror)
        monkeypatch.setattr(ga, "established_library_root", lambda: est_mirror)

        manifest = est_mirror / "PROVENANCE.toml"
        if not manifest.is_file():
            pytest.skip("established library has no PROVENANCE.toml to mutate")

        repo, vault, cache = _make_repo(tmp_path)
        settings = _settings(repo, vault, cache)

        # Build once and confirm the cache is fresh.
        ga.build_cached_graph(settings, skip_invalid_files=True)
        key1 = ga.graph_source_key(settings)
        assert ga.graph_cache_is_stale(settings) is False

        # Mutate ONLY the manifest (append a comment — no node .md touched).
        with manifest.open("a", encoding="utf-8") as f:
            f.write("\n# provenance-only bump\n")

        key2 = ga.graph_source_key(settings)
        assert key2 != key1, "manifest-only change did not change the source key"
        assert ga.graph_cache_is_stale(settings) is True, (
            "cache should be stale after a provenance-only manifest change"
        )

        # A rebuild re-freshens the recorded key.
        ga.build_cached_graph(settings, skip_invalid_files=True)
        assert ga.graph_cache_is_stale(settings) is False

    @pytest.mark.integration
    def test_missing_manifest_keeps_key_stable(self, tmp_path, monkeypatch):
        """A library with NO PROVENANCE.toml still yields a well-defined, stable
        key across calls (absence must never wedge the cache)."""
        established_root = _ensure_established_library()
        est_mirror = tmp_path / "established-mirror"
        shutil.copytree(established_root, est_mirror)
        (est_mirror / "PROVENANCE.toml").unlink(missing_ok=True)
        monkeypatch.setattr(ga, "established_library_root", lambda: est_mirror)

        repo, vault, cache = _make_repo(tmp_path)
        settings = _settings(repo, vault, cache)

        key_a = ga.graph_source_key(settings)
        key_b = ga.graph_source_key(settings)
        assert key_a == key_b and key_a, "key must be stable + well-defined w/o manifest"
