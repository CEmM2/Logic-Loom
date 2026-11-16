"""Tests for the bundled Assistant workflows.

Covers:
  1. Each of the six bundled JSON files parses as a WorkflowDefinition.
  2. ``executor``, ``source_mode``, ``prompt_slots``, ``output_format``
     populated correctly.
  3. ``list_bundled_workflows()`` returns the six new entries plus the
     inherited AKMS Node Extraction workflow (seven total).

The six bundled workflow ids:
  - assistant.literature_survey
  - assistant.paper_introduction
  - assistant.related_work
  - assistant.proposal_ideas
  - assistant.method_comparison
  - assistant.annotated_bibliography
"""

from __future__ import annotations

import json
from importlib import resources

import pytest

from logic_loom_api.workflows import (
    WORKFLOW_SUFFIX,
    WorkflowDefinition,
    list_bundled_workflows,
    load_workflow_from_json,
)

_ASSISTANT_WORKFLOW_IDS = (
    "assistant.literature_survey",
    "assistant.paper_introduction",
    "assistant.related_work",
    "assistant.proposal_ideas",
    "assistant.method_comparison",
    "assistant.annotated_bibliography",
)

_ASSISTANT_CATEGORIES = frozenset(
    {"Literature Review", "Paper Writing", "Proposal Writing", "Analysis"}
)

# Defined alongside the workflows: each Assistant workflow's main prompt slot
# id mirrors the workflow id. The ``PromptSlot`` enum carries the matching
# values; this test pins the symmetry at the workflow level.
_EXPECTED_PROMPT_SLOTS = {wid: {"main": wid} for wid in _ASSISTANT_WORKFLOW_IDS}


def _read_bundled_json(workflow_id: str) -> dict:
    """Return the parsed JSON for one bundled Assistant workflow file."""
    pkg = "logic_loom_api.resources.workflow_library.defaults"
    root = resources.files(pkg)
    candidate = root.joinpath(f"{workflow_id}{WORKFLOW_SUFFIX}")
    assert candidate.is_file(), f"missing bundled file for {workflow_id}"
    return json.loads(candidate.read_text(encoding="utf-8"))


class TestBundledAssistantWorkflows:
    """Unit tests for the bundled Assistant workflows."""

    @pytest.mark.unit
    def test_each_bundled_assistant_workflow_parses(self) -> None:
        """Verifies: every bundled file parses as WorkflowDefinition."""
        for wid in _ASSISTANT_WORKFLOW_IDS:
            raw = _read_bundled_json(wid)
            wf = load_workflow_from_json(json.dumps(raw))
            assert isinstance(wf, WorkflowDefinition)
            assert wf.id == wid
            assert wf.name, f"{wid} must have a non-empty name"
            assert wf.description, f"{wid} must have a non-empty description"

    @pytest.mark.unit
    def test_workflow_library_lists_six_new_plus_akms(self) -> None:
        """Verifies: ``list_bundled_workflows()`` returns the six new
        Assistant entries plus the inherited AKMS Node Extraction."""
        bundled = list_bundled_workflows()
        ids = {wf.id for wf in bundled}
        for wid in _ASSISTANT_WORKFLOW_IDS:
            assert wid in ids, f"bundled listing missing {wid}"
        # AKMS Node Extraction was the only pre-Assistant bundled workflow
        # gated by category != Assistant. Other research/writing demos are
        # older AKMS-bundle workflows and remain bundled too.
        assert "akms.node_extraction" in ids, "AKMS Node Extraction still bundled"

    @pytest.mark.unit
    def test_workflow_executor_and_output_format_populated(self) -> None:
        """Verifies: every Assistant workflow declares a valid
        ``executor`` and ``output_format``.

        All six bundled workflows declare ``executor="claude"`` (the actual
        executor binary) with ``source_mode="notebooklm"`` (the grounding
        source). The Assistant run dispatcher also accepts user-defined
        workflows with ``executor="notebooklm"`` for batches that do NOT
        require a claude pass — but the bundled defaults follow the
        existing AKMS pattern of claude-as-executor + notebooklm-as-source.
        """
        for wid in _ASSISTANT_WORKFLOW_IDS:
            raw = _read_bundled_json(wid)
            assert raw["executor"] == "claude", (
                f"{wid} declared executor={raw['executor']!r}; "
                "bundled defaults use claude-as-executor"
            )
            assert raw["source_mode"] == "notebooklm", (
                f"{wid} declared source_mode={raw['source_mode']!r}; "
                "bundled defaults use notebooklm grounding"
            )
            assert raw["output_format"] == "markdown", (
                f"{wid} declared output_format={raw['output_format']!r}; "
                "all six Assistant workflows produce markdown"
            )

    @pytest.mark.unit
    def test_workflow_categories_in_assistant_set(self) -> None:
        """Verifies: every bundled Assistant workflow's category is in the
        documented set {"Literature Review", "Paper Writing",
        "Proposal Writing", "Analysis"}.

        The Assistant WorkflowLibrary view filters on this exact set; a workflow
        outside it would be silently dropped from the Assistant listing.
        """
        for wid in _ASSISTANT_WORKFLOW_IDS:
            raw = _read_bundled_json(wid)
            cat = raw["category"]
            assert cat in _ASSISTANT_CATEGORIES, (
                f"{wid} category={cat!r} not in Assistant set "
                f"{sorted(_ASSISTANT_CATEGORIES)}"
            )

    @pytest.mark.unit
    def test_workflow_prompt_slots_reference_assistant_slots(self) -> None:
        """Verifies: each workflow's ``prompt_slots`` maps internal slot
        names to canonical Assistant slot ids defined in the ``PromptSlot``
        enum.

        The naming convention pins the workflow main slot to the same id
        as the workflow itself (``assistant.literature_survey`` →
        prompt slot ``assistant.literature_survey``). This eliminates the
        slot-name vs workflow-id drift that the AKMS bundle's
        ``workflow.<name>.main`` convention had to track separately.
        """
        for wid in _ASSISTANT_WORKFLOW_IDS:
            raw = _read_bundled_json(wid)
            assert raw["prompt_slots"] == _EXPECTED_PROMPT_SLOTS[wid], (
                f"{wid} prompt_slots={raw['prompt_slots']!r}; expected "
                f"{_EXPECTED_PROMPT_SLOTS[wid]!r}"
            )
