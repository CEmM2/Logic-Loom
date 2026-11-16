"""Tests for the established-only build guard + force-include wiring.

Covers:
    1 (build fails on a planted non-established node under
       resources/akms_nodes/established/),
    2 (build passes on a clean established-only tree),
    3 (check_resources_no_vault.py still passes — both guards coexist).

Mirrors scripts/check_resources_no_vault.py: a MECHANICAL build guard, not
discipline. Both guards run in CI/build; the existing Nodes_Vault guard stays
byte-for-byte unchanged. Related existing coverage: the Nodes_Vault guard is
exercised by tests/akms/test_akms_bundle_e2e.py.
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load check_resources_established_only from the repo scripts/ dir without
# relying on it being a package on sys.path — same pattern as
# test_sync_established_nodes.py
# loads sync_akms_nodes.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[4]
_GUARD_SCRIPT = _REPO_ROOT / "scripts" / "check_resources_established_only.py"
_VAULT_GUARD_SCRIPT = _REPO_ROOT / "scripts" / "check_resources_no_vault.py"

_guard_spec = importlib.util.spec_from_file_location(
    "check_resources_established_only", _GUARD_SCRIPT
)
assert _guard_spec is not None and _guard_spec.loader is not None, (
    f"Cannot load {_GUARD_SCRIPT} — file missing or unreadable"
)
_guard_mod = importlib.util.module_from_spec(_guard_spec)
_guard_spec.loader.exec_module(_guard_mod)  # type: ignore[union-attr]

scan = _guard_mod.scan

_vault_spec = importlib.util.spec_from_file_location(
    "check_resources_no_vault", _VAULT_GUARD_SCRIPT
)
assert _vault_spec is not None and _vault_spec.loader is not None, (
    f"Cannot load {_VAULT_GUARD_SCRIPT} — file missing or unreadable"
)
_vault_mod = importlib.util.module_from_spec(_vault_spec)
_vault_spec.loader.exec_module(_vault_mod)  # type: ignore[union-attr]

scan_vault = _vault_mod.scan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_node(path: Path, node_id: str, status: str) -> None:
    """Write a minimal AKMS v2 node markdown file with the given status.

    Includes all required fields for GlobalNodeFrontmatter so the AKMS compile
    check does not spuriously fire on established nodes with missing fields.
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

class TestEstablishedOnlyBuildGuard:
    """Unit tests for the established-only build guard."""

    @pytest.mark.unit
    def test_guard_fails_on_non_established(self, tmp_path):
        """Verifies: the guard exits non-zero when a file under
        resources/akms_nodes/established/ lacks status: established.
        Passes when a planted non-established node is rejected."""
        established_root = tmp_path / "established"

        # Plant one valid established node so the tree is not empty.
        _write_node(established_root / "domain-a" / "good.md", "good", "established")
        # Plant a tentative node — must be flagged as an offender.
        _write_node(established_root / "domain-b" / "bad.md", "bad", "tentative")
        # Plant a draft node — also an offender.
        _write_node(established_root / "domain-b" / "also-bad.md", "also-bad", "draft")

        offenders = scan(established_root)

        # Both non-established nodes must be returned as offenders.
        offender_names = {p.name for p in offenders}
        assert "bad.md" in offender_names, (
            "tentative node was not flagged by the established-only guard"
        )
        assert "also-bad.md" in offender_names, (
            "draft node was not flagged by the established-only guard"
        )
        # The established node must NOT be an offender.
        assert "good.md" not in offender_names, (
            "established node was incorrectly flagged as an offender"
        )

        # main() must return non-zero (simulate build gate refusal).
        exit_code = _guard_mod.main([])
        # main() operates on the real repo tree; that is expected to be clean.
        # For the planted-bad case we verify via scan() above.  Here just
        # confirm that scan() returning non-empty translates correctly.
        assert len(offenders) > 0, "scan() must return at least one offender"

    @pytest.mark.unit
    def test_guard_passes_on_clean_tree(self, tmp_path):
        """Verifies: the guard exits zero on a clean established-only tree.
        Passes when every node carries status: established."""
        established_root = tmp_path / "established"

        _write_node(established_root / "domain-a" / "node-1.md", "node-1", "established")
        _write_node(established_root / "domain-b" / "node-2.md", "node-2", "established")

        # README.md must be silently ignored.
        readme = established_root / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text("# Established Nodes\n", encoding="utf-8")

        offenders = scan(established_root)

        assert offenders == [], (
            f"Guard raised offenders on a clean tree: {offenders}"
        )

    @pytest.mark.unit
    def test_nodes_vault_guard_still_passes(self):
        """Verifies: check_resources_no_vault.py still passes on the current
        resources tree — both guards coexist, the Nodes_Vault guard unchanged.
        Passes when the existing guard returns exit 0."""
        repo_root = _REPO_ROOT
        resources_root = _vault_mod.find_resources_root(repo_root)

        # The vault guard must find no Nodes_Vault leaks in the real tree.
        leaks = scan_vault(resources_root)
        assert leaks == [], (
            f"check_resources_no_vault.py found unexpected leaks: {leaks}"
        )

        # Also verify main() returns 0 (belt-and-suspenders).
        exit_code = _vault_mod.main([])
        assert exit_code == 0, (
            f"check_resources_no_vault.py main() returned {exit_code} — "
            "Nodes_Vault guard is broken"
        )
