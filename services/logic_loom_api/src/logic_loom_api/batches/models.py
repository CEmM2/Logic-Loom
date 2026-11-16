"""Pydantic schemas for the Editable Source Batches subsystem.

``schema_version`` is pinned on :class:`BatchStore`; per-record models
inherit the same v1 contract (no migrations needed yet — a future schema
change bumps the version and adds migrations).

Models:

* :class:`BatchNode` — one prospective AKMS node attached to a batch.
* :class:`BatchGenerationDefaults` — workflow defaults stored on the batch
  (consumed by the run-workflow handler).
* :class:`EditableBatch` — the workspace-editable batch record. Carries the
  two fields the :class:`logic_loom_api.prompts.resolver.PromptBatchLike`
  Protocol requires (``prompt_profile_id``, ``prompt_overrides``) plus the
  payload the run, dispatch and UI layers consume (papers, nodes, notebook
  id, free-text context for the picker UI).

``EditableBatch`` is the **only** model in this module that satisfies
:class:`PromptBatchLike`; the ``/api/batches/{id}/effective-prompts``
endpoint hands a real ``EditableBatch`` straight to
``resolve_prompts_for_run`` with no adapter glue.

Note on round-trip stability: every field has a deterministic default
(default-factory or scalar), so ``EditableBatch.model_dump()`` is a pure
function of the user-supplied fields — required for the store's JSON
round-trip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

BatchStatus = Literal["draft", "active", "archived"]
BatchSource = Literal["imported", "user", "duplicated"]
BatchOrigin = Literal["user", "starter"]
BatchNodeStatus = Literal["planned", "generated", "validated", "promoted"]
OutputFormat = Literal["yaml", "json", "markdown"]


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Centralised so tests can monkeypatch a single call site if needed.
    """
    return datetime.now(UTC).isoformat()


class BatchNode(BaseModel):
    """A prospective AKMS node attached to a batch.

    Field defaults are part of the on-disk contract.
    """

    model_config = ConfigDict(extra="ignore")

    node_id: str
    title: str
    description: str = ""
    domain: str = ""
    tags: list[str] = Field(default_factory=list)
    priority: int = 0
    status: BatchNodeStatus = "planned"


class BatchGenerationDefaults(BaseModel):
    """Workflow defaults persisted on the batch.

    Consumed by the run-workflow handler when a user runs a workflow
    against this batch without overrides.
    """

    model_config = ConfigDict(extra="ignore")

    default_output_dir: str | None = None
    nlm_timeout: float = 540
    max_retries: int = 2
    output_format: OutputFormat = "yaml"


class EditableBatch(BaseModel):
    """Workspace-editable batch record.

    Satisfies :class:`logic_loom_api.prompts.resolver.PromptBatchLike` via
    the ``prompt_profile_id`` and ``prompt_overrides`` fields (mandatory
    for the prompt resolver contract).

    ``id`` is a stable slug derived from ``title`` by the store layer
    (collisions get a numeric suffix). The model itself does not enforce
    uniqueness — that's the store's responsibility.

    ``created_at`` / ``updated_at`` are ISO-8601 strings rather than
    :class:`datetime` to keep JSON round-trip lossless and human-readable
    in the on-disk ``batches.json`` file.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    description: str = ""
    round: int | None = None
    round_title: str = ""
    round_theme: str = ""
    status: BatchStatus = "active"
    source: BatchSource = "user"
    origin: BatchOrigin = "user"
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)
    papers: list[str] = Field(default_factory=list)
    nodes: list[BatchNode] = Field(default_factory=list)
    notebook_id: str | None = None
    zotsums_text: str = ""
    sources_text: str = ""
    notes: str = ""
    default_workflow_id: str | None = None
    # The two PromptBatchLike fields. See prompts/resolver.py docstring.
    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    generation: BatchGenerationDefaults = Field(
        default_factory=BatchGenerationDefaults
    )
