"""Tests for the established-node sync script into resources.

Covers:
    1 (running the sync produces resources/akms_nodes/established/ containing
       ONLY established nodes).

Mirrors scripts/sync_akms_claude_assets.py. The sync copies only
``status: established`` nodes from the curated source into the shippable
resources tree; anything not established is skipped at sync time (first line
of defense — the established-only build guard is the enforcement).
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load sync_akms_nodes from the repo scripts/ dir without relying on it being
# a package on sys.path — mirrors how other tests load repo-root scripts.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SYNC_SCRIPT = _REPO_ROOT / "scripts" / "sync_akms_nodes.py"

_spec = importlib.util.spec_from_file_location("sync_akms_nodes", _SYNC_SCRIPT)
assert _spec is not None and _spec.loader is not None, (
    f"Cannot load {_SYNC_SCRIPT} — file missing or unreadable"
)
_sync_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sync_mod)  # type: ignore[union-attr]

sync_nodes = _sync_mod.sync_nodes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_node(path: Path, node_id: str, status: str) -> None:
    """Write a minimal AKMS v2 node markdown file with the given status."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(f"""\
            ---
            id: {node_id}
            title: "Test Node {node_id}"
            domain: test-domain
            status: {status}
            source: human
            akms_schema: v2
            ---

            # {node_id}

            Placeholder content.
        """),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSyncEstablishedNodes:
    """Unit tests for the established-only sync into resources."""

    @pytest.mark.unit
    def test_sync_copies_only_established(self, tmp_path):
        """Verifies: running the sync over a fixture source with MIXED statuses
        (established + tentative) copies only the established nodes into the
        output tree. Passes when the output contains exactly the
        established nodes."""
        source = tmp_path / "source"
        dest = tmp_path / "dest"

        # Two established nodes in different sub-domains.
        _write_node(source / "domain-a" / "node-est-1.md", "node-est-1", "established")
        _write_node(source / "domain-b" / "node-est-2.md", "node-est-2", "established")
        # One tentative and one draft — must NOT be copied.
        _write_node(source / "domain-a" / "node-tentative.md", "node-tentative", "tentative")
        _write_node(source / "domain-b" / "node-draft.md", "node-draft", "draft")

        copied = sync_nodes(source, dest)

        # Exactly 2 established nodes should have been returned.
        assert len(copied) == 2, f"Expected 2 copied nodes, got {len(copied)}: {copied}"

        # The destination tree must contain only the established nodes.
        dest_mds = sorted(p.name for p in dest.rglob("*.md"))
        assert "node-est-1.md" in dest_mds
        assert "node-est-2.md" in dest_mds
        assert len(dest_mds) == 2, (
            f"Expected 2 .md files in dest, got {len(dest_mds)}: {dest_mds}"
        )

    @pytest.mark.unit
    def test_non_established_excluded(self, tmp_path):
        """Verifies: a non-established (e.g. tentative) node in the source is
        NOT present in the synced output tree. Passes when no
        non-established node leaks into resources/akms_nodes/established/."""
        source = tmp_path / "source"
        dest = tmp_path / "dest"

        # One established node (needed so the canary doesn't fire).
        _write_node(source / "domain-a" / "node-est.md", "node-est", "established")
        # Non-established variants.
        _write_node(source / "domain-a" / "node-tentative.md", "node-tentative", "tentative")
        _write_node(source / "domain-b" / "node-draft.md", "node-draft", "draft")
        _write_node(source / "domain-b" / "node-deprecated.md", "node-deprecated", "deprecated")

        copied = sync_nodes(source, dest)

        # Exactly 1 node copied (the established one).
        assert len(copied) == 1

        # Non-established nodes must NOT appear in the destination.
        dest_names = {p.name for p in dest.rglob("*.md")}
        assert "node-tentative.md" not in dest_names, (
            "tentative node leaked into the established sync output"
        )
        assert "node-draft.md" not in dest_names, (
            "draft node leaked into the established sync output"
        )
        assert "node-deprecated.md" not in dest_names, (
            "deprecated node leaked into the established sync output"
        )
        assert "node-est.md" in dest_names

    @pytest.mark.unit
    def test_demoted_node_removed_on_resync(self, tmp_path):
        """Ship-safety regression: a node that was established on the first sync
        but is later removed (or demoted) from the source must NOT linger in the
        output tree on a re-sync. The shipped tree is a faithful mirror of the
        CURRENT established source — the sync is a clean rebuild, so a stale
        ``status: established`` copy cannot survive to ship."""
        source = tmp_path / "source"
        dest = tmp_path / "dest"

        # First sync: two established nodes.
        _write_node(source / "domain-a" / "keeper.md", "keeper", "established")
        _write_node(source / "domain-a" / "goner.md", "goner", "established")
        first = sync_nodes(source, dest)
        assert len(first) == 2
        first_names = {p.name for p in dest.rglob("*.md")}
        assert first_names == {"keeper.md", "goner.md"}

        # Demote 'goner' to tentative (equivalent to removal from the shipped
        # tree) and re-sync.
        _write_node(source / "domain-a" / "goner.md", "goner", "tentative")
        second = sync_nodes(source, dest)

        assert len(second) == 1
        second_names = {p.name for p in dest.rglob("*.md")}
        assert "goner.md" not in second_names, (
            "demoted node lingered in dest after re-sync — stale established "
            "copy would ship"
        )
        assert second_names == {"keeper.md"}

    @pytest.mark.unit
    def test_deleted_node_removed_on_resync(self, tmp_path):
        """Ship-safety regression: a node deleted outright from the source must
        be GONE from the output tree after a re-sync (clean rebuild)."""
        source = tmp_path / "source"
        dest = tmp_path / "dest"

        _write_node(source / "domain-a" / "keeper.md", "keeper", "established")
        _write_node(source / "domain-b" / "removed.md", "removed", "established")
        sync_nodes(source, dest)
        assert {p.name for p in dest.rglob("*.md")} == {"keeper.md", "removed.md"}

        # Delete 'removed' from the source entirely, then re-sync.
        (source / "domain-b" / "removed.md").unlink()
        second = sync_nodes(source, dest)

        assert len(second) == 1
        second_names = {p.name for p in dest.rglob("*.md")}
        assert "removed.md" not in second_names, (
            "deleted node lingered in dest after re-sync"
        )
        assert second_names == {"keeper.md"}
