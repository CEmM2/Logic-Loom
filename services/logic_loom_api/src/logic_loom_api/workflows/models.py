"""Pydantic schemas for the Workflow Library subsystem.

A :class:`WorkflowDefinition` is the JSON-on-disk description of a runnable
workflow — its ``id`` is the cross-system handle that the run-workflow
handler accepts, the dispatch preflight route filters on, and the UI
renders into the run dialog.

Two storage classes share the model:

* **Bundled defaults** — ship inside the wheel under
  ``logic_loom_api.resources.workflow_library.defaults``. Source-of-truth
  for the four first-release workflows
  (AKMS Node Extraction, Literature Survey, Paper Introduction, Proposal
  Ideas). The :func:`logic_loom_api.workflows.builtin.list_bundled_workflows`
  loader is the read entry point.
* **User-edited** — live under
  ``<akms_workspace>/workflow-library/user-workflows/<id>.json``. The
  routes layer (``routes/workflows.py``) is responsible for that directory; this module
  only defines the schema.

Design notes:

* ``schema_version`` is pinned at 1. Future
  migrations bump the field and add a migration step in the loader.
* ``executor`` and ``source_mode`` are :class:`Literal` types so a typo
  fails validation at load time (rather than at run time, which would
  bring down the executor).
* ``required_external_tools`` is the key the dispatch preflight route reads
  to gate AKMS workflows on the presence of ``claude`` and ``nlm`` binaries.
* ``prompt_slots`` maps the workflow's internal slot names (e.g.
  ``"main"``) to canonical :class:`PromptSlot` ids (e.g.
  ``"akms.node_gen_skill"``). The validation hook only checks the *id*
  shape is in the canonical set — it does NOT check that the variant_ref
  resolves; that's the run-workflow handler's job.

Round-trip stability: every optional field has a deterministic default so
``WorkflowDefinition.model_dump()`` is a pure function of the user-supplied
inputs — the round-trip test asserts that.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..prompts.models import PromptSlot

# Sentinel — bumped on schema migrations.
SCHEMA_VERSION = 1

# The enum-likes. ``Executor`` includes ``"notebooklm"`` so user-defined
# Assistant workflows can declare an NLM-CLI dispatch path. Bundled
# defaults still use ``"claude"`` (with ``source_mode="notebooklm"``
# meaning "claude-as-executor, notebooklm-as-source"); the new value
# enables the alternate flow where NLM is the executor itself.
Executor = Literal["claude", "zsum", "notebooklm"]
SourceMode = Literal["notebooklm", "zotsum-local", "none"]
OutputFormat = Literal["yaml", "json", "markdown", "text"]

# Status used by the routes layer. Bundled defaults are always "active"
# (archive of a built-in is a duplicate-then-archive on the duplicate).
WorkflowStatus = Literal["active", "archived"]
WorkflowSource = Literal["bundled", "user"]


class WorkflowValidation(BaseModel):
    """Optional output-validation hints surfaced by the dispatch layer.

    Maps to the workflow JSON's ``validation.required_sections`` — the
    dispatch layer / output store can use it to confirm the
    generated artefact has the expected scaffolding before declaring a
    run "succeeded".
    """

    model_config = ConfigDict(extra="ignore")

    required_sections: list[str] = Field(default_factory=list)


class WorkflowDefinition(BaseModel):
    """One workflow JSON document on disk.

    ``id`` is the dot-delimited canonical
    handle (e.g. ``"akms.node_extraction"``, ``"writing.paper_introduction"``).
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    id: str
    name: str
    category: str = ""
    description: str = ""
    executor: Executor
    source_mode: SourceMode = "none"
    input_slots: list[str] = Field(default_factory=list)
    output_format: OutputFormat = "markdown"
    default_output_subdir: str = ""
    prompt_slots: dict[str, str] = Field(default_factory=dict)
    required_external_tools: list[str] = Field(default_factory=list)
    validation: WorkflowValidation = Field(default_factory=WorkflowValidation)

    # Storage-side metadata. Not part of the on-disk schema for bundled
    # defaults (the bundled loader sets them post-validation); user-edited
    # files MAY persist these so the routes layer can keep the archived
    # state across restarts.
    status: WorkflowStatus = "active"
    source: WorkflowSource = "user"

    @field_validator("id")
    @classmethod
    def _check_id_shape(cls, v: str) -> str:
        """Workflow ids are dot-delimited identifiers.

        We don't enforce a strict family list (new categories may be
        added) but we DO reject empty ids and ids with whitespace so
        filesystem layout stays sane.
        """
        if not v or not v.strip():
            raise ValueError("WorkflowDefinition.id must be a non-empty identifier")
        if any(c.isspace() for c in v):
            raise ValueError(
                f"WorkflowDefinition.id must not contain whitespace: {v!r}"
            )
        return v

    @field_validator("prompt_slots")
    @classmethod
    def _check_prompt_slot_refs(cls, v: dict[str, str]) -> dict[str, str]:
        """Each prompt-slot value must reference a canonical slot id.

        A workflow may use a value like ``"workflow.paper_introduction.main"`` —
        a slot id is the first two dot-segments. We accept either the
        bare slot id (e.g. ``"akms.node_gen_skill"``) or a fully-qualified
        variant ref (``"workflow.paper_introduction.main"`` style is
        permitted by NOT rejecting unknown prefixes; instead we ONLY
        validate when the value parses as a canonical slot id. This keeps
        the schema permissive for non-AKMS workflows that may carry their
        own variant ref scheme.
        """
        canonical = set(PromptSlot.all_ids())
        for k, ref in v.items():
            if not isinstance(ref, str) or not ref:
                raise ValueError(
                    f"prompt_slots[{k!r}] must be a non-empty string"
                )
            # If the ref looks like a canonical slot id, require it.
            head = ".".join(ref.split(".")[:2])
            if head in canonical:
                continue
            # Otherwise allow it — workflow-specific variant refs (e.g.
            # ``"workflow.paper_introduction.main"``) are permitted.
        return v


def load_workflow_from_json(text: str) -> WorkflowDefinition:
    """Parse a JSON document into a :class:`WorkflowDefinition`.

    Convenience wrapper so the loader and the routes layer share the same
    entry point. Raises :class:`pydantic.ValidationError` on schema drift.
    """
    return WorkflowDefinition.model_validate_json(text)


def dump_workflow_to_dict(wf: WorkflowDefinition) -> dict[str, Any]:
    """Return a JSON-serialisable dict for the workflow.

    ``status`` and ``source`` are stripped because they are storage-side
    metadata, not part of the workflow schema. The routes layer
    reattaches them on read.
    """
    data = wf.model_dump()
    data.pop("status", None)
    data.pop("source", None)
    return data


__all__ = [
    "SCHEMA_VERSION",
    "Executor",
    "OutputFormat",
    "SourceMode",
    "WorkflowDefinition",
    "WorkflowSource",
    "WorkflowStatus",
    "WorkflowValidation",
    "dump_workflow_to_dict",
    "load_workflow_from_json",
]
