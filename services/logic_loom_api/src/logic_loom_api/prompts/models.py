"""Pydantic models for the prompt library.

``schema_version = 1`` is pinned on every model — a future schema change
bumps it and attaches migrations.

The fourteen canonical slot ids — 8 AKMS-bundle plus 6 Assistant:

* ``zsum.paper_card``
* ``zsum.collection_digest``
* ``zsum.collection_digest_structured``
* ``zsum.collection_redigest``
* ``zsum.restructure``
* ``akms.node_gen_skill``
* ``akms.node_gen_command``
* ``akms.digest_refiner_agent``
* ``assistant.literature_survey``
* ``assistant.paper_introduction``
* ``assistant.related_work``
* ``assistant.proposal_ideas``
* ``assistant.method_comparison``
* ``assistant.annotated_bibliography``

These are addressed via :class:`PromptSlot`. The string form (e.g.
``"akms.node_gen_skill"``) is the on-disk identifier.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class PromptSlot(str, Enum):
    """The canonical prompt slots — 8 AKMS-bundle plus 6 Assistant.

    Inheriting from :class:`str` keeps JSON serialisation straightforward —
    ``PromptSlot.AKMS_NODE_GEN_SKILL.value == "akms.node_gen_skill"`` and the
    enum is JSON-serialisable as a plain string.
    """

    # zsum family (5 slots — runtime targets under ``prompts/*.txt``).
    ZSUM_PAPER_CARD = "zsum.paper_card"
    ZSUM_COLLECTION_DIGEST = "zsum.collection_digest"
    ZSUM_COLLECTION_DIGEST_STRUCTURED = "zsum.collection_digest_structured"
    ZSUM_COLLECTION_REDIGEST = "zsum.collection_redigest"
    ZSUM_RESTRUCTURE = "zsum.restructure"

    # akms family (3 slots — runtime targets under ``.claude/...``).
    AKMS_NODE_GEN_SKILL = "akms.node_gen_skill"
    AKMS_NODE_GEN_COMMAND = "akms.node_gen_command"
    AKMS_DIGEST_REFINER_AGENT = "akms.digest_refiner_agent"

    # assistant family (6 slots — runtime targets under
    # ``prompt_library/defaults/assistant/<slot>/default.md``).
    ASSISTANT_LITERATURE_SURVEY = "assistant.literature_survey"
    ASSISTANT_PAPER_INTRODUCTION = "assistant.paper_introduction"
    ASSISTANT_RELATED_WORK = "assistant.related_work"
    ASSISTANT_PROPOSAL_IDEAS = "assistant.proposal_ideas"
    ASSISTANT_METHOD_COMPARISON = "assistant.method_comparison"
    ASSISTANT_ANNOTATED_BIBLIOGRAPHY = "assistant.annotated_bibliography"

    @classmethod
    def all_ids(cls) -> tuple[str, ...]:
        """Return the 14 slot ids in deterministic declaration order.

        Used by tests and the resolver to assert exhaustive coverage of the
        canonical set. The tuple is intentionally a fresh value per call so
        callers can mutate it without affecting the enum.
        """
        return tuple(s.value for s in cls)


# Slot family inferred from the leading dot-segment of a slot id.
SlotFamily = Literal["zsum", "akms", "assistant"]


def slot_family(slot_id: str) -> SlotFamily:
    """Return the family (``"zsum"`` / ``"akms"`` / ``"assistant"``) for a slot id.

    Used for filesystem layout under ``variants/<family>/<slot_stem>/``.
    Raises :class:`ValueError` if ``slot_id`` is not one of the canonical
    ids in :class:`PromptSlot`.
    """
    if slot_id not in PromptSlot.all_ids():
        raise ValueError(f"unknown prompt slot id: {slot_id!r}")
    family, _ = slot_id.split(".", 1)
    # mypy-friendly: cast through Literal via runtime check.
    if family == "zsum":
        return "zsum"
    if family == "assistant":
        return "assistant"
    return "akms"


class PromptVariant(BaseModel):
    """An editable instruction template for one specific slot.

    Stored under
    ``<workspace>/prompt-library/variants/<family>/<slot_stem>/<id>.logicloom.json``.

    Field semantics:

    * ``id`` — stable slug, unique per slot. Derived from ``name`` if not
      supplied (handled by the store layer, not the model).
    * ``family`` — ``"zsum"`` or ``"akms"``. Must match the leading segment
      of ``slot``. Validated by :func:`PromptVariant.model_post_init`.
    * ``slot`` — one of :class:`PromptSlot`'s ids.
    * ``source`` — ``"bundled"`` (ships in the wheel) or ``"user"``
      (lives in the workspace and is editable).
    * ``based_on`` — optional ``"bundled:<slot>.<id>"`` provenance reference.
    * ``filename`` — runtime target relative path (e.g.
      ``".claude/skills/node-gen/SKILL.md"`` or ``"prompts/paper_card.txt"``).
    * ``content_type`` — ``"markdown"`` or ``"text"``.
    * ``content_hash`` — SHA-256 hex digest of ``content`` prefixed with
      ``"sha256:"``. Not enforced at model level (the store layer computes it
      on write).
    * ``content`` — the actual prompt text/markdown.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    id: str = Field(min_length=1)
    family: SlotFamily
    slot: str
    name: str = Field(min_length=1)
    description: str = ""
    source: Literal["bundled", "user"] = "user"
    based_on: str | None = None
    filename: str = Field(min_length=1)
    content_type: Literal["markdown", "text"] = "markdown"
    content_hash: str = ""
    content: str = ""
    # ``output_schema_id`` references a :class:`PromptSchema` by its ``id``.
    # ``None`` means the variant ships its schema inline in ``content`` (legacy
    # behavior) or has no structured-output schema at all. Setting it lets the
    # same variant be paired with different schemas without rewriting prose.
    output_schema_id: str | None = None

    def model_post_init(self, __context) -> None:  # noqa: D401 — Pydantic hook
        """Validate that ``family`` matches the ``slot`` family prefix.

        Raises :class:`ValueError` so Pydantic re-wraps as a
        :class:`pydantic.ValidationError`.
        """
        expected = slot_family(self.slot)
        if self.family != expected:
            raise ValueError(
                f"family={self.family!r} does not match slot family {expected!r} "
                f"for slot {self.slot!r}"
            )


class PromptSchema(BaseModel):
    """A reusable JSON schema decoupled from prompt content.

    Variants that produce structured output (e.g. ``zsum.paper_card``) can
    reference a :class:`PromptSchema` by ``id``. Storing schemas separately
    lets the user pair the same prompt with different output schemas (or the
    same schema with different prompts) without copy-pasting either.

    Stored on disk under ``<workspace>/prompt-library/schemas/<id>.schema.json``
    and shipped bundled under
    ``resources/prompt_library/defaults/schemas/<id>.schema.json``.

    The ``content`` field is the JSON Schema *as a dict*; serialisation
    round-trips through ``model_dump_json`` so the on-disk file is a normal
    JSON document the user can hand-edit.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    source: Literal["bundled", "user"] = "user"
    # Schemas are scoped to a slot family so the AKMS prompts page
    # does not show zsum's PaperCard schema. ``None`` means unscoped — the
    # schema appears under every family. Bundled schemas are tagged at
    # load time from the in-file ``x-logicloom-family`` key (or inferred
    # via the slot id ``output_schema_id`` they were authored for).
    family: Literal["zsum", "akms", "assistant"] | None = None
    content: dict = Field(default_factory=dict)


class PromptProfile(BaseModel):
    """A bundle of prompt variants assigned to slots.

    Stored under
    ``<workspace>/prompt-library/profiles/<id>.json``.

    The ``active`` dict maps slot id (string) → variant reference. Variant
    references are formed as ``"<source>:<slot>.<variant_id>"`` (e.g.
    ``"user:akms.node_gen_skill.strict-equations"``).

    Profiles do **not** need to be exhaustive — a profile may declare a
    subset of slots. The resolver layers on top of ``global_profile.active``
    so any slots absent from a profile fall through to the global default
    (or the bundled default if nothing else activates them).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    active: dict[str, str] = Field(default_factory=dict)


class ActivePromptProfile(BaseModel):
    """The globally-active slot→variant_ref map.

    Read from ``<workspace>/prompt-library/active-profile.json``. This is the
    tier-1 layer in :func:`resolve_prompts_for_run`.

    Distinct from :class:`PromptProfile` because the active profile has no
    independent ``id`` / ``name`` — it's a pointer to one of the stored
    profiles plus any user-pinned per-slot overrides.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    profile_id: str | None = None
    active: dict[str, str] = Field(default_factory=dict)


class EffectivePromptSet(BaseModel):
    """The resolved slot→content map for a single run (resolver output).

    Returned by :func:`resolve_prompts_for_run`. Carries both the slot→
    variant_id map (audit trail; what to record in the run snapshot) and
    the slot→content map (what to write into the runtime).

    The two maps share the same keys; ``contents`` is the in-memory image of
    the chosen variants' ``content`` fields, keyed by slot id.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    slot_to_variant: dict[str, str] = Field(default_factory=dict)
    contents: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "ActivePromptProfile",
    "EffectivePromptSet",
    "PromptProfile",
    "PromptSchema",
    "PromptSlot",
    "PromptVariant",
    "SCHEMA_VERSION",
    "SlotFamily",
    "slot_family",
]
