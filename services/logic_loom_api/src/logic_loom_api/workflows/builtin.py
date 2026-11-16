"""Loader for the four bundled workflow definitions.

The JSON files live in the wheel as resources under
``logic_loom_api.resources.workflow_library.defaults``. They are *read-only*
on disk — user duplicates of a built-in are written to
``<akms_workspace>/workflow-library/user-workflows/`` by the workflows
routes layer.

The four first-release workflows:

* ``akms.node_extraction`` — generate AKMS YAML/Markdown nodes from a batch.
* ``research.literature_survey`` — synthesize themes/methods/limitations.
* ``writing.paper_introduction`` — draft a technical introduction from a batch.
* ``research.proposal_ideas`` — extract research directions and aims.

The loader follows the same degrade-gracefully pattern as
:func:`logic_loom_api.prompts.store.PromptStore.list_bundled_variants` — a
missing resources package returns an empty list rather than raising.
"""

from __future__ import annotations

import logging
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Iterable

from pydantic import ValidationError

from .models import WorkflowDefinition, load_workflow_from_json

logger = logging.getLogger(__name__)

BUNDLED_RESOURCE_PACKAGE = "logic_loom_api.resources.workflow_library.defaults"
WORKFLOW_SUFFIX = ".json"


def _bundled_root() -> Traversable | None:
    """Return the resources Traversable, or None if the package is absent.

    The defaults package may be absent (e.g. a partial dev-from-source
    checkout). Dev-from-source without the resources should not crash
    the loader.
    """
    try:
        return resources.files(BUNDLED_RESOURCE_PACKAGE)
    except (ModuleNotFoundError, FileNotFoundError):
        return None


def list_bundled_workflows() -> list[WorkflowDefinition]:
    """Return the bundled workflow definitions in deterministic order.

    Sort key: workflow id. Stable sort means the routes layer can rely on
    a consistent listing across calls without an explicit `sorted()`.

    Malformed JSON / schema drift is logged at WARNING and the bad file is
    skipped (we never let one broken default kill the listing).
    """
    root = _bundled_root()
    if root is None:
        return []
    out: list[WorkflowDefinition] = []
    try:
        children = list(root.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    for child in sorted(children, key=lambda c: c.name):
        if not child.name.endswith(WORKFLOW_SUFFIX):
            continue
        try:
            text = child.read_text(encoding="utf-8")
            wf = load_workflow_from_json(text)
        except (OSError, ValidationError, ValueError) as exc:
            logger.warning(
                "skipping malformed bundled workflow %s: %s", child.name, exc
            )
            continue
        wf.source = "bundled"
        wf.status = "active"
        out.append(wf)
    # Sort by id so callers get a deterministic order independent of FS
    # iteration order.
    out.sort(key=lambda w: w.id)
    return out


def bundled_ids() -> tuple[str, ...]:
    """Return the ids of the bundled workflows (sorted)."""
    return tuple(w.id for w in list_bundled_workflows())


def iter_bundled() -> Iterable[WorkflowDefinition]:
    """Module-level convenience for callers that prefer iteration."""
    return iter(list_bundled_workflows())


__all__ = [
    "BUNDLED_RESOURCE_PACKAGE",
    "WORKFLOW_SUFFIX",
    "bundled_ids",
    "iter_bundled",
    "list_bundled_workflows",
]
