"""Regression test for the Learn integration's halt-gate note (+ its resolution).

The first Learn integration shipped only the baseline generation modes and was
gated ("halted") until the vendored akms-learn snapshot provided the full mode
catalog. This test verifies that the halt-gate dev note exists at the expected
path within the Logic-Loom repo, documents all five halt criteria (verified by a
stable keyword per criterion), preserves the literal resume-TODO marker (see
``_RESUME_TODO``; the note's original wording) as an audit-trail anchor, AND
records the gate's resolution (the gate was LIFTED on 2026-06-09 once the
vendored akms-learn snapshot was confirmed to ship the full catalog).

If the note is removed, any criterion / the TODO marker is deleted, or the
gate-lift record is reverted (e.g. back to a bare "HALTED" state), this test fails —
keeping the documented gate AND its resolution load-bearing.

Halt criteria:
1. Learn tab appears only when plugin is installed.
2. Baseline modes compile + preview works.
3. Bundle export works.
4. Graph tab works without plugin (no regression).
5. The resume-TODO gate is documented in Logic-Loom dev notes.

Gate status: LIFTED — the precondition (full akms-learn catalog available) is
satisfied by the vendored akms-learn snapshot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo-root resolution — robust: walk up from this file's location.
# This file lives at:
#   <repo_root>/services/logic_loom_api/tests/test_learn_halt_gate.py
# So parents[3] is the repo root.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
_NOTE_PATH = _REPO_ROOT / "dev" / "notes" / "pass_a_halt_gate.md"

# Stable keywords — one per criterion — that must appear in the note.
# Each keyword is tied to that criterion's OWN prose section heading or a
# distinctive phrase unique to that section, so removing ANY single criterion's
# prose section (while leaving all other sections intact) will fail the
# corresponding assertion independently of the _RESUME_TODO assertion.
_CRITERION_KEYWORDS: list[tuple[str, str]] = [
    # (criterion label, keyword that proves it is documented)
    ("Criterion 1 — tab gating", "LearnGate"),
    ("Criterion 2 — compile + preview", "compileLearningSource"),
    ("Criterion 3 — bundle export", "KNOWN_EXPORTERS"),
    ("Criterion 4 — graph non-regression", "TestGraphRouteNonRegression"),
    # Criterion 5's anchor is its OWN section heading — distinct from the
    # _RESUME_TODO string which lives in the separate "Resume gate" section.
    # Deleting the Criterion 5 prose section removes the heading; deleting the
    # Resume gate section removes _RESUME_TODO.  Both are asserted independently.
    ("Criterion 5 — resume TODO", "Criterion 5 — Documented resume TODO gate"),
]

_RESUME_TODO = "TODO: resume after akms-learn Plan 2/3"

# Markers proving the gate's RESOLUTION is recorded (the gate was lifted once the
# vendored akms-learn snapshot was confirmed to ship the full catalog). Reverting the note
# to a bare "HALTED" state (deleting the lift record) must fail the test.
_GATE_LIFTED_MARKERS: list[str] = [
    "Gate status: LIFTED",
    "Resume gate — RESOLVED",
]


class TestLearnHaltGateNote:
    @pytest.mark.regression
    def test_halt_gate_note_exists_with_criteria_and_resume_todo(self) -> None:
        """Verifies: the Learn halt-gate dev note exists, lists the 5 criteria, and
        contains the literal resume-TODO marker (``_RESUME_TODO``).

        Requirement: All five halt criteria documented; resume TODO present.
        """
        if not (_REPO_ROOT / "dev").is_dir():
            pytest.skip("dev/ is internal and not part of the published source tree")
        assert _NOTE_PATH.is_file(), (
            f"Halt-gate note not found at expected path: {_NOTE_PATH}\n"
            f"  Repo root resolved to: {_REPO_ROOT}\n"
            f"  Restore the note; it records the Learn halt gate and its resolution."
        )

        content = _NOTE_PATH.read_text(encoding="utf-8")

        # Assert all five criteria keywords are present.
        for label, keyword in _CRITERION_KEYWORDS:
            assert keyword in content, (
                f"Halt-gate note is missing evidence for {label!r}.\n"
                f"  Expected keyword: {keyword!r}\n"
                f"  Note path: {_NOTE_PATH}"
            )

        # Assert the literal resume-TODO marker is present (the test greps for
        # this exact string — keep it verbatim in the note as the audit anchor,
        # even now that it is marked RESOLVED).
        assert _RESUME_TODO in content, (
            f"Halt-gate note is missing the literal resume marker:\n"
            f"  Expected: {_RESUME_TODO!r}\n"
            f"  Note path: {_NOTE_PATH}"
        )

        # Assert the gate's RESOLUTION is recorded — the gate was lifted once the
        # vendored akms-learn snapshot was confirmed to ship the full catalog. Reverting
        # the note to a bare "HALTED" state (dropping these markers) must fail.
        for marker in _GATE_LIFTED_MARKERS:
            assert marker in content, (
                f"Halt-gate note is missing the gate-resolution marker {marker!r}.\n"
                f"  The gate was LIFTED (2026-06-09) — the note must record that, not\n"
                f"  just the original halt. Note path: {_NOTE_PATH}"
            )
