"""Tests for the draft→established promotion workflow.

Covers:
    1 (a promoted node lands in the curated tree with valid frontmatter
       and passes the guard),
    2 (an incomplete promotion — missing required frontmatter — is caught
       by the guard).

Mirrors scripts/promote_node.py: a promotion tool that takes a Tier-B node,
stamps it with status: established + PROVENANCE.toml frontmatter, and moves it
into the curated verified_nodes/ tree. The backstop is
scripts/check_resources_established_only.py — an incomplete promotion fails
the build.

The promotion checklist defines what 'established' means in practice; human
review remains the gate. Both guards (Nodes_Vault exclusion + established-only)
coexist and run in CI/build.
"""

from __future__ import annotations

import importlib.util
import textwrap
import tomllib
from pathlib import Path

import frontmatter
import pytest


# ---------------------------------------------------------------------------
# Load promote_node and check_resources_established_only from repo scripts
# without relying on them being packages on sys.path — same pattern as
# test_sync_established_nodes.py and test_established_only_build_guard.py.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[4]
_PROMOTE_SCRIPT = _REPO_ROOT / "scripts" / "promote_node.py"
_GUARD_SCRIPT = _REPO_ROOT / "scripts" / "check_resources_established_only.py"

_promote_spec = importlib.util.spec_from_file_location(
    "promote_node", _PROMOTE_SCRIPT
)
_guard_spec = importlib.util.spec_from_file_location(
    "check_resources_established_only", _GUARD_SCRIPT
)

# Guard must exist.
if _guard_spec is None or _guard_spec.loader is None:
    raise AssertionError(
        f"Cannot load {_GUARD_SCRIPT} — file missing or unreadable"
    )

_guard_mod = importlib.util.module_from_spec(_guard_spec)
_guard_spec.loader.exec_module(_guard_mod)  # type: ignore[union-attr]

scan = _guard_mod.scan

# Load promote_node the same way.
if _promote_spec is None or _promote_spec.loader is None:
    raise AssertionError(
        f"Cannot load {_PROMOTE_SCRIPT} — file missing or unreadable"
    )

_promote_mod = importlib.util.module_from_spec(_promote_spec)
_promote_spec.loader.exec_module(_promote_mod)  # type: ignore[union-attr]

promote_node = _promote_mod.promote_node
PromotionError = _promote_mod.PromotionError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_draft_node(path: Path, node_id: str, status: str = "tentative") -> None:
    """Write a minimal, valid AKMS v2 draft (Tier-B) node markdown file.

    Includes all required fields for GlobalNodeFrontmatter so the AKMS compile
    check keys only on ``status`` (the promotion gate), not on a spurious
    structural defect. Provenance intentionally does NOT live in frontmatter —
    GlobalNodeFrontmatter is ``extra="forbid"``, so provenance lives in the
    PROVENANCE.toml sidecar (written by promote_node).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(f"""\
            ---
            id: {node_id}
            title: "Test Node {node_id}"
            domain: test-domain
            status: {status}
            source: human
            tags:
              - test
            confidence: 0.9
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

class TestEstablishedPromotion:
    """Tests for the draft→established promotion workflow."""

    @pytest.mark.unit
    def test_promoted_node_stamped_and_guard_passes(self, tmp_path):
        """Verifies: a promoted node in curated tree with frontmatter passes guard.
        Passes when: guard scan on established/ with a valid node succeeds.

        Drives the REAL promote_node() end to end against a tmp curated tree,
        then drives the REAL guard scan() over the tree promote_node produced.
        No mocks: this asserts behavior, not calls.
        """
        # A Tier-B draft node living outside the curated tree.
        src = tmp_path / "inbox" / "test-node.md"
        _write_draft_node(src, "test-node", status="tentative")

        curated = tmp_path / "verified_nodes"

        dest = promote_node(
            src,
            curated,
            domain="test-domain",
            verified_by="reviewer-x",
            verified_at="2026-07-01",
            version="1.0",
            source_ref="Doe (2026), Test Reference, Ch. 1.",
        )

        # 1. The node landed under the curated tree, grouped by domain.
        assert dest.exists()
        assert dest == curated / "test-domain" / "test-node.md"

        # 2. Frontmatter was stamped status: established (and NOT carrying
        #    provenance keys — those are forbidden in frontmatter).
        post = frontmatter.load(str(dest))
        assert post.metadata["status"] == "established"
        for forbidden in ("verified_by", "verified_at", "version", "source_ref", "provenance"):
            assert forbidden not in post.metadata

        # 3. Provenance landed in the PROVENANCE.toml sidecar, keyed by node id.
        prov_path = curated / "PROVENANCE.toml"
        assert prov_path.exists()
        prov = tomllib.loads(prov_path.read_text(encoding="utf-8"))
        assert prov["test-node"] == {
            "verified_by": "reviewer-x",
            "verified_at": "2026-07-01",
            "version": "1.0",
            "source_ref": "Doe (2026), Test Reference, Ch. 1.",
        }

        # 4. The REAL guard passes over the promoted node. The guard scans the
        #    established resources tree, so mirror the promoted node into a
        #    guard-shaped tmp tree and assert scan() finds no offenders.
        established_root = tmp_path / "established"
        (established_root / "test-domain").mkdir(parents=True)
        (established_root / "test-domain" / "test-node.md").write_text(
            dest.read_text(encoding="utf-8"), encoding="utf-8"
        )
        assert scan(established_root) == []

    @pytest.mark.unit
    def test_incomplete_promotion_caught_by_guard(self, tmp_path):
        """Verifies: missing frontmatter (status or provenance) causes guard to fail.
        Passes when: guard scan on established/ with a malformed node exits non-zero.

        Two failure routes:
          (a) promote_node refuses an incomplete promotion up front (missing
              required provenance field) — PromotionError.
          (b) if a node with a non-established status slips into the established
              tree anyway, the REAL guard scan() flags it as an offender.
        """
        src = tmp_path / "inbox" / "bad-node.md"
        _write_draft_node(src, "bad-node", status="tentative")
        curated = tmp_path / "verified_nodes"

        # (a) promote_node rejects a promotion with a missing provenance field
        #     and does NOT write a partial/incomplete node into the curated tree.
        with pytest.raises(PromotionError):
            promote_node(
                src,
                curated,
                domain="test-domain",
                verified_by="reviewer-x",
                verified_at="2026-07-01",
                version="1.0",
                source_ref="",  # empty required field → incomplete promotion
            )
        assert not (curated / "test-domain" / "bad-node.md").exists()
        assert not (curated / "PROVENANCE.toml").exists()

        # (b) the guard is the backstop: a node that reaches the established
        #     tree WITHOUT status: established is caught by the real scan().
        established_root = tmp_path / "established"
        offender = established_root / "test-domain" / "bad-node.md"
        _write_draft_node(offender, "bad-node", status="tentative")
        offenders = scan(established_root)
        assert offender in offenders
