"""Tests for the curated established-library tree + frontmatter contract.

Covers:
    1 (fixture established nodes are valid AKMS v2 nodes that COMPILE),
    2 (status == established + ship-provenance present in PROVENANCE.toml),
    3 (curated tree is distinct from any Nodes_Vault path — the vault is never
       shipped).

Tier A (established) nodes are read-only and gated by ``status: established``.
The curated tree is a NEW, separate tree from vendored Nodes_Vault.

The contract is exactly the AKMS v2 node schema: every node must pass
``akms.schema.validators`` (``GlobalNodeFrontmatter``) — the same validator the
graph compiler uses in ``graph_adapter.scan_graph_sources``.  Because
``GlobalNodeFrontmatter`` forbids extra keys (``extra="forbid"``), ship-
provenance (verified_by / verified_at / version / source_ref) cannot live in
the node frontmatter; it lives in a sidecar ``PROVENANCE.toml`` keyed by
node id.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import frontmatter  # type: ignore[import-not-found,import-untyped]
import pytest

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

# services/logic_loom_api/tests/bundled_nodes_and_batches/ is 4
# levels below the repo root (parents[4]).
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Curated source tree.
CURATED_ROOT = _REPO_ROOT / "verified_nodes"

# Ship-provenance sidecar manifest.
PROVENANCE_PATH = CURATED_ROOT / "PROVENANCE.toml"

# Required provenance keys per node id in the manifest.
REQUIRED_PROVENANCE_KEYS = {"verified_by", "verified_at", "version", "source_ref"}


def _collect_fixture_nodes() -> list[Path]:
    """Return all node .md files under the curated tree.

    Excludes documentation markdown that is not a node — README.md and
    PROMOTION_CHECKLIST.md both live under this tree but are docs, not nodes.
    Rather than maintain a growing name blocklist, a curated-tree ``.md`` file
    counts as a node only if its frontmatter carries an ``id`` (the node
    identifier every real node has and no doc does). PROVENANCE.toml is not a
    ``.md`` file so it is naturally excluded.
    """
    if not CURATED_ROOT.is_dir():
        return []
    nodes: list[Path] = []
    for p in sorted(CURATED_ROOT.rglob("*.md")):
        try:
            post = frontmatter.load(str(p))
        except Exception:
            # Malformed frontmatter must not be silently dropped — a real node
            # with broken YAML should surface as a failure in the assertions,
            # so keep it in the collection.
            nodes.append(p)
            continue
        if post.metadata.get("id"):
            nodes.append(p)
    return nodes


def _load_provenance() -> dict:
    """Parse PROVENANCE.toml → {node_id: {verified_by, ...}}."""
    with PROVENANCE_PATH.open("rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEstablishedLibraryContract:
    """Unit tests for the curated tree + frontmatter contract."""

    @pytest.mark.unit
    def test_fixture_nodes_parse(self):
        """Verifies: every placeholder node is a valid AKMS v2 node that
        COMPILES against ``akms.schema.validators`` (``GlobalNodeFrontmatter``)
        — the same validator the graph compiler uses.

        Skips gracefully if the AKMS extra is not installed (mirrors the
        graph tests), so core-only CI stays green."""
        pytest.importorskip("akms")
        from akms.schema import validators  # noqa: WPS433 (import inside test)

        nodes = _collect_fixture_nodes()
        assert nodes, (
            f"No fixture .md files found under {CURATED_ROOT}. "
            "The curated tree must contain at least 3 nodes."
        )
        errors: list[str] = []
        for path in nodes:
            metadata = dict(frontmatter.load(str(path)).metadata)
            try:
                # global-vault kwargs (is_local=False, is_code_mirror=False) —
                # exactly what graph_adapter.scan_graph_sources passes for the
                # global vault source.
                validators.parse_node_frontmatter_from_dict(
                    metadata, path=str(path)
                )
            except Exception as exc:  # SchemaValidationError et al.
                errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        assert not errors, (
            "Fixture nodes failed AKMS schema validation:\n" + "\n".join(errors)
        )

    @pytest.mark.unit
    def test_all_fixtures_established(self):
        """Verifies: every fixture node has status == 'established' AND
        compiles as an established AKMS node. Passes when no
        non-established node exists in the curated tree."""
        pytest.importorskip("akms")
        from akms.schema import validators  # noqa: WPS433
        from akms.schema.models import NodeStatus  # noqa: WPS433

        nodes = _collect_fixture_nodes()
        assert nodes, f"No fixture nodes found under {CURATED_ROOT}."
        non_established: list[str] = []
        for path in nodes:
            metadata = dict(frontmatter.load(str(path)).metadata)
            node = validators.parse_node_frontmatter_from_dict(
                metadata, path=str(path)
            )
            # node.status is a NodeStatus enum; compare by value so the check
            # is robust whether it deserializes as the enum or a bare str.
            if node.status != NodeStatus.ESTABLISHED:
                non_established.append(f"{path.name}: status={node.status!r}")
        assert not non_established, (
            "Non-established nodes found in the curated tree (only "
            "'established' nodes may live here):\n"
            + "\n".join(non_established)
        )

    @pytest.mark.unit
    def test_provenance_manifest_covers_every_node(self):
        """Verifies: every fixture node id has a matching entry in
        PROVENANCE.toml carrying the required ship-provenance keys
        (verified_by, verified_at, version, source_ref).

        Ship-provenance cannot live in the node frontmatter
        (GlobalNodeFrontmatter forbids extra keys), so the sidecar manifest is
        the contract for it. This test needs no akms import — it reads the
        node id straight from the YAML frontmatter."""
        assert PROVENANCE_PATH.is_file(), (
            f"Ship-provenance manifest missing: {PROVENANCE_PATH}"
        )
        provenance = _load_provenance()

        nodes = _collect_fixture_nodes()
        assert nodes, f"No fixture nodes found under {CURATED_ROOT}."

        problems: list[str] = []
        for path in nodes:
            metadata = dict(frontmatter.load(str(path)).metadata)
            node_id = metadata.get("id")
            if not node_id:
                problems.append(f"{path.name}: node has no 'id' in frontmatter")
                continue
            entry = provenance.get(node_id)
            if entry is None:
                problems.append(
                    f"{path.name}: id={node_id!r} has no PROVENANCE.toml entry"
                )
                continue
            missing = REQUIRED_PROVENANCE_KEYS - entry.keys()
            if missing:
                problems.append(
                    f"{path.name}: provenance entry {node_id!r} missing "
                    f"{sorted(missing)}"
                )
        assert not problems, (
            "Ship-provenance manifest problems:\n" + "\n".join(problems)
        )

    @pytest.mark.unit
    def test_curated_tree_distinct_from_nodes_vault(self):
        """Verifies: the curated tree path contains no 'Nodes_Vault' path
        component (Non-Goal #1 — separate tree). Passes when the curated
        root is not under and does not include any Nodes_Vault component."""
        curated_parts = CURATED_ROOT.resolve().parts
        assert "Nodes_Vault" not in curated_parts, (
            f"Curated tree {CURATED_ROOT} must not live under any "
            f"'Nodes_Vault' directory component. Found in: {curated_parts}"
        )
        # Also verify the path string itself contains no Nodes_Vault segment.
        assert "Nodes_Vault" not in str(CURATED_ROOT), (
            f"Curated root path contains 'Nodes_Vault': {CURATED_ROOT}"
        )
        # Confirm the tree exists (it is checked into the repo).
        assert CURATED_ROOT.is_dir(), (
            f"Curated tree {CURATED_ROOT} does not exist."
        )
