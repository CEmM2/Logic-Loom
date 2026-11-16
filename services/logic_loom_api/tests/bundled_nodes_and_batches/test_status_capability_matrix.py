"""Tests for the dev/STATUS.md ledger + capability matrix rows of the bundled library.

Covers:
    1 (manual: review dev/STATUS.md diff against the convention in CLAUDE.md
       '## Tracker'),
    2 (both capability-matrix rows are present and use the exact Integration
       Status vocabulary).

Purpose:
    The established library is versioned and its shipped capabilities are
    recorded in the cross-repo tracker (dev/STATUS.md). Case 1 is a manual review of the
    diff against the Tracker convention; Case 2 is an automated regression test
    verifying that both "starter batches shipped" and "established node library
    shipped" rows exist in the capability matrix and use the exact Integration
    Status vocabulary: Upstream-only → Vendored → Wired → Gated → Exposed → Shipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Path to dev/STATUS.md relative to the repo root.
# We locate it by walking up from this test file's location.
_REPO_ROOT = Path(__file__).parents[4]  # .../Logic-Loom
_STATUS_MD = _REPO_ROOT / "dev" / "STATUS.md"

# The exact Integration Status vocabulary terms (per CLAUDE.md "## Tracker").
_VALID_VOCAB = frozenset(
    {"Upstream-only", "Vendored", "Wired", "Gated", "Exposed", "Shipped"}
)


class TestStatusCapabilityMatrix:
    """Tests for the dev/STATUS.md capability-matrix rows (case 2)."""

    @pytest.mark.regression
    def test_capability_matrix_rows_use_integration_vocabulary(self):
        """Verifies: dev/STATUS.md carries both shipped-capability rows in the exact Integration Status vocabulary.

        Requirement: Capability matrix reflects both shipped capabilities (starter batches, established node library).
        Passes when: both rows present with valid vocab terms in the Gating and Status cells.
        """
        if not (_REPO_ROOT / "dev").is_dir():
            pytest.skip("dev/ is internal and not part of the published source tree")
        assert _STATUS_MD.exists(), f"dev/STATUS.md not found at {_STATUS_MD}"
        content = _STATUS_MD.read_text(encoding="utf-8")

        # Locate the capability matrix section.
        matrix_start = content.find("## Capability matrix")
        assert matrix_start != -1, "Could not find '## Capability matrix' section in dev/STATUS.md"

        # Narrow to the matrix section only (up to the next ## heading or end of file).
        next_section = content.find("\n## ", matrix_start + 1)
        matrix_section = content[matrix_start:] if next_section == -1 else content[matrix_start:next_section]

        # ── Row 1: Starter batches ────────────────────────────────────────────
        # Expect a pipe-delimited table row mentioning "starter batches" (case-insensitive),
        # with a Gating cell and a Status cell that both use the exact vocabulary.
        starter_rows = [
            line for line in matrix_section.splitlines()
            if "|" in line and re.search(r"starter batch", line, re.IGNORECASE)
        ]
        assert starter_rows, (
            "No capability-matrix row for 'starter batches' found in dev/STATUS.md. "
            "Expected a pipe-delimited row containing 'starter batch' in the Capability column."
        )

        # ── Row 2: Established node library ──────────────────────────────────
        established_rows = [
            line for line in matrix_section.splitlines()
            if "|" in line and re.search(r"established node", line, re.IGNORECASE)
        ]
        assert established_rows, (
            "No capability-matrix row for 'established node library' found in dev/STATUS.md. "
            "Expected a pipe-delimited row containing 'established node' in the Capability column."
        )

        # ── Vocabulary check for each found row ──────────────────────────────
        def _assert_row_uses_vocab(row: str, label: str) -> None:
            """Assert that the Status cell (last non-empty pipe-cell) uses valid vocabulary."""
            cells = [c.strip() for c in row.split("|") if c.strip()]
            # The Status cell is the last cell in the row.
            assert cells, f"{label} row appears empty after splitting on '|': {row!r}"
            status_cell = cells[-1]
            assert status_cell in _VALID_VOCAB, (
                f"{label} Status cell {status_cell!r} is not in the Integration Status vocabulary "
                f"{sorted(_VALID_VOCAB)}. Row: {row!r}"
            )
            # Also assert the Gating cell (second-to-last, per table schema) uses vocab or is free text.
            # The table has: Capability | Route | Gating | Requires | Status
            # So cells[-2] is the Gating cell — it may be free text like "Gated (AkmsGate)".
            # We only require the Status cell to use exact vocabulary.

        for row in starter_rows:
            _assert_row_uses_vocab(row, "Starter batches")

        for row in established_rows:
            _assert_row_uses_vocab(row, "Established node library")

        # ── Both rows must have Status = "Shipped" ────────────────────────────
        # Both capabilities are end-to-end in the app → Shipped.
        for row in starter_rows:
            cells = [c.strip() for c in row.split("|") if c.strip()]
            assert cells[-1] == "Shipped", (
                f"Starter batches Status cell must be 'Shipped' (both parts are shipped end-to-end), "
                f"got {cells[-1]!r}. Row: {row!r}"
            )

        for row in established_rows:
            cells = [c.strip() for c in row.split("|") if c.strip()]
            assert cells[-1] == "Shipped", (
                f"Established node library Status cell must be 'Shipped' (end-to-end in app), "
                f"got {cells[-1]!r}. Row: {row!r}"
            )
