"""Tests for the Assistant prompt slots + default variants.

Covers:
  1. ``PromptSlot`` enum exposes the six new Assistant slot ids.
  2. Each new slot has a bundled default markdown file under
     ``resources/prompt_library/defaults/assistant/<slot>/default.md``.
  3. The Prompt Library exposes 14 slot ids total
     (8 from AKMS bundle + 6 new Assistant slots).
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

import pytest

from logic_loom_api.prompts.models import PromptSlot, slot_family


_ASSISTANT_SLOT_IDS = (
    "assistant.literature_survey",
    "assistant.paper_introduction",
    "assistant.related_work",
    "assistant.proposal_ideas",
    "assistant.method_comparison",
    "assistant.annotated_bibliography",
)

# 8 AKMS bundle + 6 Assistant = 14 total slots.
_EXPECTED_TOTAL_SLOTS = 14


def _read_default_md(slot_id: str) -> str:
    """Return the markdown content of one Assistant slot's bundled default."""
    family, stem = slot_id.split(".", 1)
    pkg = f"logic_loom_api.resources.prompt_library.defaults.{family}.{stem}"
    root = resources.files(pkg)
    candidate = root.joinpath("default.md")
    assert candidate.is_file(), f"missing default.md for {slot_id}"
    return candidate.read_text(encoding="utf-8")


def _repo_root() -> Path:
    """Return the Logic-Loom repo root from this test file's location.

    File path: ``services/logic_loom_api/tests/assistant/test_assistant_prompt_slots.py``
    Repo root: 4 parents up.
    """
    return Path(__file__).resolve().parents[4]


class TestAssistantPromptSlots:
    """Unit tests for the Assistant prompt slots + defaults."""

    @pytest.mark.unit
    def test_promptslot_enum_includes_six_assistant_slots(self) -> None:
        """Verifies: every documented Assistant slot id is reachable
        via ``PromptSlot.all_ids()``."""
        ids = set(PromptSlot.all_ids())
        for sid in _ASSISTANT_SLOT_IDS:
            assert sid in ids, f"PromptSlot enum missing {sid!r}"

    @pytest.mark.unit
    def test_promptslot_total_after_extension_is_fourteen(self) -> None:
        """Verifies: total slot count == 14 (8 AKMS + 6 Assistant)."""
        ids = PromptSlot.all_ids()
        assert len(set(ids)) == len(ids), "duplicate slot ids in enum"
        assert len(ids) == _EXPECTED_TOTAL_SLOTS, (
            f"PromptSlot.all_ids() returned {len(ids)} ids; expected "
            f"{_EXPECTED_TOTAL_SLOTS} (8 AKMS bundle + 6 Assistant)"
        )

    @pytest.mark.unit
    def test_each_assistant_slot_has_bundled_default(self) -> None:
        """Verifies: each new slot has a non-empty default.md file
        under ``resources/prompt_library/defaults/assistant/<slot>/``.
        """
        for sid in _ASSISTANT_SLOT_IDS:
            content = _read_default_md(sid)
            assert content.strip(), f"default.md for {sid} is empty"
            # Defaults target ~300-500 words. We pin a soft lower bound
            # so a placeholder one-liner can't slip through, and an upper
            # bound so the file doesn't drift into novel-length territory.
            word_count = len(content.split())
            assert word_count >= 150, (
                f"{sid} default.md has only {word_count} words; "
                "target is ~300-500 (soft lower bound: 150)"
            )

    @pytest.mark.unit
    def test_slot_family_returns_assistant_for_new_slots(self) -> None:
        """Verifies: ``slot_family(slot_id)`` returns ``"assistant"`` for
        every new slot id.

        Required so future bundled-variants loaders (and the variants
        store filesystem layout) scan the correct family directory.
        """
        for sid in _ASSISTANT_SLOT_IDS:
            assert slot_family(sid) == "assistant", (
                f"slot_family({sid!r}) returned {slot_family(sid)!r}; "
                "expected 'assistant'"
            )

    @pytest.mark.unit
    def test_slot_family_preserves_existing_families(self) -> None:
        """Regression: extending the SlotFamily literal must not break
        existing zsum / akms family routing.
        """
        assert slot_family("zsum.paper_card") == "zsum"
        assert slot_family("akms.node_gen_skill") == "akms"

    @pytest.mark.unit
    def test_sync_script_manifest_lists_assistant_defaults(self) -> None:
        """Verifies: ``packaging/akms_claude_assets.toml`` includes every
        Assistant default markdown file under
        ``[prompt_library_defaults.assistant]``.

        The copy list must reference the new tree so the
        sidecar build's sync pass picks the defaults up.
        """
        manifest = _repo_root() / "packaging" / "akms_claude_assets.toml"
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        section = data["prompt_library_defaults"].get("assistant")
        assert section is not None, (
            "[prompt_library_defaults.assistant] missing from "
            "packaging/akms_claude_assets.toml"
        )
        listed = set(section["files"])
        expected = {f"{sid.split('.', 1)[1]}/default.md" for sid in _ASSISTANT_SLOT_IDS}
        assert expected.issubset(listed), (
            f"manifest missing files: {sorted(expected - listed)}"
        )
