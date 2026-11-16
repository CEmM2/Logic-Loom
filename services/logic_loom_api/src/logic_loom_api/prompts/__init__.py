"""Prompt Library backend.

The prompt library models the user-editable overlay of prompts that get
materialised into per-run sidecar runtimes.

Public surface:

* :class:`PromptSlot` — enum of the canonical slot ids.
* :class:`PromptVariant` — Pydantic model for a single editable prompt
  variant. `schema_version = 1`.
* :class:`PromptProfile` — Pydantic model for a bundle of variants
  assigned to slots (a "profile"). `schema_version = 1`.
* :class:`ActivePromptProfile` — light wrapper representing the
  globally-active slot→variant_id map (read from ``active-profile.json``).
* :class:`PromptStore` — on-disk JSON store under
  ``<akms_workspace>/prompt-library/{variants,profiles}/``.
* :func:`resolve_prompts_for_run` — six-tier resolution.

The on-disk layout::

    <akms_workspace>/prompt-library/
      variants/
        zsum/
          paper_card/
            default.logicloom.json
            ...
        akms/
          node_gen_skill/
            ...
      profiles/
        akms-default.json
        ...
      active-profile.json

Bundled defaults ship under
``services/logic_loom_api/src/logic_loom_api/resources/prompt_library/defaults/``
and are picked up by the bundled asset manifest
(``scripts/sync_akms_claude_assets.py``).
"""

from __future__ import annotations

from .models import (
    ActivePromptProfile,
    EffectivePromptSet,
    PromptProfile,
    PromptSlot,
    PromptVariant,
)
from .resolver import (
    PromptBatchLike,
    make_batch_view,
    make_store_loaders,
    parse_variant_ref,
    resolve_prompts_for_run,
)
from .store import PromptStore

__all__ = [
    "ActivePromptProfile",
    "EffectivePromptSet",
    "PromptBatchLike",
    "PromptProfile",
    "PromptSlot",
    "PromptStore",
    "PromptVariant",
    "make_batch_view",
    "make_store_loaders",
    "parse_variant_ref",
    "resolve_prompts_for_run",
]
