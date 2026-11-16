"""AKMS workspace bootstrap.

On first sidecar startup we materialise the Logic-Loom-owned ``akms_workspace``
directory tree so downstream code (asset manager, prompt store, batch store,
workflow store, run history, dispatch) can assume the paths exist.

The bootstrap is idempotent — re-running on an existing workspace is a no-op
(directories use ``mkdir(parents=True, exist_ok=True)`` and the sentinel is
only written if absent).

A ``.logic-loom-workspace-ok`` sentinel file at the workspace root records
``schema_version: 1`` so future migrations can detect existing trees.

CLAUDE.md degrade-gracefully invariant: callers (the FastAPI ``lifespan``) MUST
wrap ``bootstrap_akms_workspace`` in ``try/except`` — a bootstrap failure must
never block the sidecar coming up. The sidecar reports
``LOGIC_LOOM_API_READY:<port>`` regardless of whether the workspace was
materialised; the function logs but does not re-raise on internal directory
errors (each ``mkdir`` runs independently, errors are logged per-dir).

First-run seeding: after the workspace tree is materialised,
:func:`_seed_starter_batches` seeds the batch store with the shipped starter
batches when the store is empty. The whole step is try/except-guarded so a
seed failure never blocks the sidecar.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from .config import Settings

logger = logging.getLogger(__name__)

# Sentinel file at the workspace root. schema_version starts at 1; bump it
# when a migration is added.
SENTINEL_NAME = ".logic-loom-workspace-ok"
SCHEMA_VERSION = 1

# Workspace subdirectories. Relative paths from workspace root. The order is
# deterministic so logs read top-down.
WORKSPACE_SUBDIRS: tuple[str, ...] = (
    "prompt-library/variants",
    "prompt-library/profiles",
    "workflow-library/user-workflows",
    "Sources_Evals/NLM/Inputs",
    "Sources_Evals/NLM/Outputs",
    "generated_nodes",
    "runs",
    "AKMS_Sources/new",
    "claude-runtime",
    "zsum-runtime",
)

# Package path for the shipped starter batches (the seed pack).
# Mirror the BUNDLED_RESOURCE_PACKAGE pattern from prompts/store.py and
# workflows/builtin.py — never hand-compute the path.
_SEED_RESOURCE_PACKAGE = "logic_loom_api.resources.akms_batches.defaults"
_SEED_SUFFIX = ".json"


def _seed_starter_batches(workspace: Path) -> None:
    """Seed the batch store with the shipped starter batches.

    Runs only when ``<workspace>/batches.json`` holds zero batches (first-run
    UX). Each seed is inserted by its own ``id``; if an id already exists
    the seed is silently skipped — this makes re-seeding a true no-op and
    ensures user-authored batches with colliding ids are never overwritten.

    Degrade-gracefully: callers wrap this function in ``try/except``.  Any
    exception that escapes is logged at WARNING by the caller; the sidecar
    must always come up regardless of seed health.
    """
    # Lazy import to keep module-level deps minimal; BatchStore lives in
    # the batches sub-package which is always available.
    from .batches.models import EditableBatch
    from .batches.store import BatchStore

    store = BatchStore.load(workspace)
    if store.batches:
        # Non-empty store — never seed, never clobber.
        logger.debug(
            "akms_workspace: batch store non-empty (%d batches), skipping seed",
            len(store.batches),
        )
        return

    # Load the defaults package.
    try:
        pkg_root = resources.files(_SEED_RESOURCE_PACKAGE)
    except (ModuleNotFoundError, FileNotFoundError):
        logger.warning(
            "akms_workspace: seed package %r not found, skipping seed",
            _SEED_RESOURCE_PACKAGE,
        )
        return

    try:
        children = sorted(pkg_root.iterdir(), key=lambda c: c.name)
    except (FileNotFoundError, NotADirectoryError) as exc:
        logger.warning("akms_workspace: cannot iterate seed package: %s", exc)
        return

    seeded = 0
    for child in children:
        if not child.name.endswith(_SEED_SUFFIX):
            continue
        try:
            text = child.read_text(encoding="utf-8")
            batch = EditableBatch.model_validate_json(text)
        except (OSError, ValidationError, ValueError) as exc:
            logger.warning(
                "akms_workspace: skipping malformed seed file %s: %s",
                child.name,
                exc,
            )
            continue
        # Skip if this id is already present (should not happen on an empty
        # store, but guard against any race or partial prior seed).
        if store.get(batch.id) is not None:
            logger.debug(
                "akms_workspace: seed id %r already present, skipping",
                batch.id,
            )
            continue
        store.add(batch)
        seeded += 1

    if seeded:
        store.save(workspace)
        logger.info(
            "akms_workspace: seeded %d starter batch(es) into %s",
            seeded,
            workspace,
        )
    else:
        logger.debug("akms_workspace: no seed files found or all skipped")


def bootstrap_akms_workspace(settings: Settings) -> Path:
    """Materialise the AKMS workspace tree under ``settings.akms_workspace``.

    Returns the resolved workspace root path (with ``~`` expanded). The return
    value is informational — callers don't usually need it.

    Idempotent: safe to call on an already-bootstrapped workspace. Existing
    files and user-modified contents are NOT overwritten; the sentinel is only
    written when absent so user-edited schema_version metadata survives.

    Per-subdir errors are logged at WARNING and do not raise. Top-level errors
    (e.g. cannot create the workspace root itself) propagate so the lifespan's
    ``try/except`` wrapper can catch and log.
    """
    workspace = settings.akms_workspace.expanduser()
    # Top-level workspace dir. If this fails the lifespan catches.
    workspace.mkdir(parents=True, exist_ok=True)

    for sub in WORKSPACE_SUBDIRS:
        try:
            (workspace / sub).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Log and continue — one bad subdir shouldn't kill the rest.
            logger.warning("akms_workspace: failed to create %s: %s", sub, exc)

    sentinel = workspace / SENTINEL_NAME
    if not sentinel.exists():
        try:
            sentinel.write_text(json.dumps({"schema_version": SCHEMA_VERSION}, indent=2) + "\n")
        except OSError as exc:
            logger.warning("akms_workspace: failed to write sentinel %s: %s", sentinel, exc)

    # Seed the batch store with shipped starter batches on first run.
    # Degrade-gracefully: a seed failure logs at WARNING and never raises.
    if settings.seed_starter_batches:
        try:
            _seed_starter_batches(workspace)
        except Exception as exc:  # noqa: BLE001
            logger.warning("akms_workspace: starter batch seeding failed: %s", exc)

    logger.info("akms_workspace bootstrapped at %s", workspace)
    return workspace
