"""Assistant workspace bootstrap.

On first sidecar startup we materialise the Assistant-owned data tree so
downstream code (batches, discovery cache, related cache, runs/outputs,
research index) can assume the paths exist.

Mirrors ``logic_loom_api.akms_workspace`` in style and degrade-gracefully
discipline:

- Idempotent — re-running on an existing workspace is a no-op (directories
  use ``mkdir(parents=True, exist_ok=True)``; seed JSON files are only
  written when absent so user-edited content survives).
- A ``.logic-loom-assistant-ok`` sentinel records ``schema_version: 1``
  for future migrations.
- Per-subdir / per-seed errors are logged at WARNING and do not raise.
  Top-level errors (e.g. cannot create the workspace root itself)
  propagate so the lifespan's ``try/except`` wrapper can catch and log.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import Settings

logger = logging.getLogger(__name__)

# Sentinel at workspace root. Bump SCHEMA_VERSION when migration logic is
# introduced.
SENTINEL_NAME = ".logic-loom-assistant-ok"
SCHEMA_VERSION = 1

# Workspace subdirectories. Deterministic order so log output reads top-down.
# discovery_cache, related_cache, runs, outputs and index are pure
# directories with no required seed file.
WORKSPACE_SUBDIRS: tuple[str, ...] = (
    "discovery_cache",
    "related_cache",
    "runs",
    "outputs",
    "index",
)

# Seed JSON files: batches.json and workflows.json need an empty file where
# the schema requires it; prompt_profiles.json is seeded too for
# forward-compatibility with prompt profiles. All three start as an empty
# JSON object `{}`; the owning services pin them to specific schemas
# (e.g. the batch registry's envelope).
SEED_JSON_FILES: tuple[tuple[str, object], ...] = (
    ("batches.json", {}),
    ("workflows.json", {}),
    ("prompt_profiles.json", {}),
)


def bootstrap_assistant_workspace(settings: Settings) -> Path:
    """Materialise the Assistant workspace tree.

    Resolves the workspace root via ``settings.assistant_workspace_path()``,
    creates each subdirectory and seed JSON file if absent, and writes
    the schema sentinel.

    Returns the resolved workspace root (informational — callers usually
    do not need the return value).
    """
    workspace = settings.assistant_workspace_path()
    # Top-level workspace dir. If this fails the lifespan wrapper catches.
    workspace.mkdir(parents=True, exist_ok=True)

    for sub in WORKSPACE_SUBDIRS:
        try:
            (workspace / sub).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("assistant_workspace: failed to create %s: %s", sub, exc)

    for name, default in SEED_JSON_FILES:
        target = workspace / name
        if target.exists():
            continue
        try:
            target.write_text(json.dumps(default, indent=2) + "\n")
        except OSError as exc:
            logger.warning("assistant_workspace: failed to seed %s: %s", name, exc)

    sentinel = workspace / SENTINEL_NAME
    if not sentinel.exists():
        try:
            sentinel.write_text(
                json.dumps({"schema_version": SCHEMA_VERSION}, indent=2) + "\n"
            )
        except OSError as exc:
            logger.warning(
                "assistant_workspace: failed to write sentinel %s: %s", sentinel, exc
            )

    logger.info("assistant_workspace bootstrapped at %s", workspace)
    return workspace
