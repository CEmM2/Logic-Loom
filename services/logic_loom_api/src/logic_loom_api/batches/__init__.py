"""Editable Source Batches.

Public re-exports for the batches package:

* :mod:`.models` — Pydantic schemas (``BatchNode``, ``BatchGenerationDefaults``,
  ``EditableBatch``, ``BatchStore``).
* :mod:`.store` — on-disk JSON store rooted at
  ``<akms_workspace>/batches.json``.
* :mod:`.compat` — legacy ``generation_plan.md`` import/export bridge.

``routes/batches.py`` is the HTTP surface, and the picker adapter reads
from this store.

The schema is pinned at ``schema_version = 1`` on every model — future
schema changes bump it and ship migrations.
"""

from __future__ import annotations

from .models import (
    SCHEMA_VERSION,
    BatchGenerationDefaults,
    BatchNode,
    BatchOrigin,
    BatchSource,
    BatchStatus,
    EditableBatch,
)
from .store import BatchStore, slugify_title

__all__ = [
    "SCHEMA_VERSION",
    "BatchGenerationDefaults",
    "BatchNode",
    "BatchOrigin",
    "BatchSource",
    "BatchStatus",
    "BatchStore",
    "EditableBatch",
    "slugify_title",
]
