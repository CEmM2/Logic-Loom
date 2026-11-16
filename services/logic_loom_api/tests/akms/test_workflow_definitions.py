"""Workflow definition schema + bundled workflows.

Covers:
  All four bundled workflows parse and validate.
  ``executor`` / ``source_mode`` / ``prompt_slots`` populated correctly,
  and AKMS workflows declare ``required_external_tools=['claude','nlm']``.

Tier: unit.
"""

from __future__ import annotations

import pytest

from logic_loom_api.workflows import (
    WorkflowDefinition,
    bundled_ids,
    list_bundled_workflows,
)


# The AKMS bundle ships these four ids. The Assistant mode ADDS six
# ``assistant.*`` workflows so the bundled listing grows additively.
# Tests below check `EXPECTED_IDS` is a subset of
# `list_bundled_workflows()`, not equality — the same additive-extension
# pattern as test_prompt_slots_schema.py's
# `test_slot_enumeration_eight_ids`.
EXPECTED_IDS = {
    "akms.node_extraction",
    "research.literature_survey",
    "writing.paper_introduction",
    "research.proposal_ideas",
}


class TestWorkflowDefinitions:
    """Bundled workflow definitions parse and validate."""

    @pytest.mark.unit
    def test_load_and_validate_each_bundled_workflow(self):
        """All four bundled workflows parse and validate.

        Verifies both module-level requirements in one pass:
          * all four ids surface from :func:`list_bundled_workflows`
          * each parses as a :class:`WorkflowDefinition`
          * required AKMS preflight key is set on the AKMS workflow
          * ``executor`` / ``source_mode`` / ``prompt_slots`` are populated
        """
        wfs = list_bundled_workflows()
        ids = {w.id for w in wfs}
        # Subset assertion — the Assistant mode adds more.
        assert EXPECTED_IDS.issubset(ids), (
            f"missing AKMS-bundle ids: {EXPECTED_IDS - ids}"
        )

        # bundled_ids() also a subset.
        assert EXPECTED_IDS.issubset(set(bundled_ids()))

        for w in wfs:
            assert isinstance(w, WorkflowDefinition)
            # ``executor`` literal was widened by the Assistant run
            # dispatcher to include "notebooklm" — keep test permissive.
            assert w.executor in ("claude", "zsum", "notebooklm")
            assert w.source_mode in ("notebooklm", "zotsum-local", "none")
            assert isinstance(w.prompt_slots, dict)
            assert w.prompt_slots, f"{w.id} has empty prompt_slots"
            assert w.source == "bundled"
            assert w.status == "active"

        # AKMS workflows must declare required_external_tools.
        akms = next(w for w in wfs if w.id == "akms.node_extraction")
        assert set(akms.required_external_tools) >= {"claude", "nlm"}
        # AKMS node extraction must reference the three canonical AKMS slots.
        assert set(akms.prompt_slots.values()) >= {
            "akms.node_gen_skill",
            "akms.node_gen_command",
            "akms.digest_refiner_agent",
        }

    @pytest.mark.unit
    def test_workflow_definition_rejects_empty_id(self):
        """Id validator rejects whitespace / empty ids."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkflowDefinition(
                id="",
                name="x",
                executor="claude",
            )
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                id="bad id",
                name="x",
                executor="claude",
            )

    @pytest.mark.unit
    def test_workflow_definition_roundtrip(self):
        """``model_dump`` -> ``model_validate`` is the identity."""
        wf = WorkflowDefinition(
            id="test.example",
            name="Test",
            executor="claude",
            source_mode="none",
            prompt_slots={"main": "akms.node_gen_skill"},
        )
        again = WorkflowDefinition.model_validate(wf.model_dump())
        assert again.id == wf.id
        assert again.prompt_slots == wf.prompt_slots
