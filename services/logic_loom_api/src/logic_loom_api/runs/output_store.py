"""Per-run output layout + metadata snapshot.

Owns the on-disk shape under ``<akms_workspace>/outputs/<batch_id>/<workflow_id>/<run_id>/``
(output layout + run metadata). The canonical structure:

    outputs/
      <batch_id>/
        <workflow_id>/
          <run_id>/
            metadata.json
            logs.txt
            prompt_snapshot/
              effective_prompts.json
              <slot_id>.<ext>

``run_id`` follows the format ``run_<YYYY-MM-DD_HHMM>_<slug-of-label>`` so the
list is sortable by name and human-readable. The slug is derived from
``run_label`` (or ``run`` when no label is supplied); non-slug characters are
normalised to ``-`` and consecutive separators collapsed.

The store is intentionally stateless — every operation is a static method
that touches disk. The run-history routes read these files back; the SSE
log stream tails ``logs.txt``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from ..prompts.models import EffectivePromptSet

logger = logging.getLogger(__name__)


_RUN_ID_RE = re.compile(r"^run_\d{4}-\d{2}-\d{2}_\d{4}_[a-z0-9][a-z0-9-]*$")
_SLUG_KEEP = re.compile(r"[^a-z0-9]+")

#: Snapshot subdirectory name.
SNAPSHOT_DIRNAME = "prompt_snapshot"
#: Combined effective_prompts.json file (lives inside ``prompt_snapshot/``).
#: Filename is underscore-cased to match the Assistant runner's snapshot
#: convention; the HTTP route remains ``/effective-prompts`` (REST style).
EFFECTIVE_PROMPTS_FILE = "effective_prompts.json"
#: Run metadata file (lives at the run-dir root).
METADATA_FILENAME = "metadata.json"
#: Log file the executor appends to and the SSE endpoint tails.
LOG_FILENAME = "logs.txt"


def _slugify_label(label: str | None) -> str:
    """Lowercase, alnum-and-dash slug. ``None``/empty -> ``"run"``."""
    if not label:
        return "run"
    s = _SLUG_KEEP.sub("-", label.strip().lower()).strip("-")
    return s or "run"


def make_run_id(run_label: str | None, *, now: datetime | None = None) -> str:
    """Mint a run_id of the form ``run_<YYYY-MM-DD_HHMM>_<slug>``.

    Deterministic given the same ``now`` + ``run_label``; the registry uses
    this to address the on-disk directory and the registry index entry.
    """
    ts = (now or datetime.now(UTC)).strftime("%Y-%m-%d_%H%M")
    return f"run_{ts}_{_slugify_label(run_label)}"


def is_valid_run_id(run_id: str) -> bool:
    """``True`` if ``run_id`` matches the canonical ``run_<ts>_<slug>`` shape."""
    return bool(_RUN_ID_RE.match(run_id))


def _ext_for_slot(slot_id: str) -> str:
    """File extension for a slot snapshot. Markdown-by-default; ``zsum.*`` -> ``.txt``.

    For example ``akms.node_gen_skill.md`` and
    ``zsum.collection_digest.txt`` — this function encodes that policy.
    """
    if slot_id.startswith("zsum."):
        return ".txt"
    return ".md"


class OutputStore:
    """Static helpers for the per-run output directory + metadata snapshot.

    Method contract:

    * :meth:`create_run_dir` mints the run directory tree and returns its
      :class:`Path`. Idempotent within a single ``now`` value; callers that
      need a fresh id should pass a fresh ``run_label`` or a different
      ``now`` slice.
    * :meth:`write_metadata` writes ``metadata.json`` atomically.
    * :meth:`write_prompt_snapshot` writes per-slot files + the combined
      ``effective_prompts.json``.
    """

    @staticmethod
    def base_dir(workspace: Path) -> Path:
        """``<workspace>/outputs``. The canonical outputs root."""
        return workspace / "outputs"

    @staticmethod
    def run_dir_for(
        workspace: Path, *, batch_id: str, workflow_id: str, run_id: str
    ) -> Path:
        """Compute the canonical run directory path without creating it."""
        return (
            OutputStore.base_dir(workspace) / batch_id / workflow_id / run_id
        )

    @staticmethod
    def create_run_dir(
        workspace: Path,
        *,
        batch_id: str,
        workflow_id: str,
        run_label: str | None = None,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[str, Path]:
        """Create the per-run directory tree.

        Returns ``(run_id, run_dir)``. The minted ``run_id`` follows
        :func:`make_run_id` unless a caller passes an explicit ``run_id``
        (used by the workflow handler so the registry entry and the
        on-disk directory share an id).

        Side effect: ``run_dir`` and ``run_dir / prompt_snapshot/`` are
        created on disk (``parents=True, exist_ok=True``). An empty
        ``logs.txt`` is touched so the SSE endpoint has something to tail
        before the executor starts writing.
        """
        rid = run_id or make_run_id(run_label, now=now)
        run_dir = OutputStore.run_dir_for(
            workspace,
            batch_id=batch_id,
            workflow_id=workflow_id,
            run_id=rid,
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / SNAPSHOT_DIRNAME).mkdir(parents=True, exist_ok=True)
        # Stake out the log file so SSE tails it from the start.
        log_path = run_dir / LOG_FILENAME
        if not log_path.exists():
            log_path.touch()
        return rid, run_dir

    # ------------------------------------------------------------------
    # Metadata snapshot
    # ------------------------------------------------------------------

    @staticmethod
    def write_metadata(run_dir: Path, payload: Mapping[str, Any]) -> Path:
        """Write ``metadata.json`` atomically (tempfile + rename).

        Returns the absolute path to the written file. ``payload`` is
        serialised with ``json.dumps(..., indent=2, default=str)`` so
        :class:`Path` / :class:`datetime` round-trip without exploding.
        """
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / METADATA_FILENAME
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(dict(payload), indent=2, default=str, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(target)
        return target

    @staticmethod
    def read_metadata(run_dir: Path) -> dict[str, Any]:
        """Read ``metadata.json`` back. Raises :class:`FileNotFoundError` on miss."""
        target = run_dir / METADATA_FILENAME
        return json.loads(target.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Prompt snapshot
    # ------------------------------------------------------------------

    @staticmethod
    def write_prompt_snapshot(
        run_dir: Path, effective_prompts: EffectivePromptSet
    ) -> Path:
        """Persist the per-run prompt snapshot.

        Writes one file per slot (``<slot_id>.<ext>``) under
        ``prompt_snapshot/`` plus a combined ``effective_prompts.json``
        carrying both the slot→variant_ref map and the slot→content map.
        The two maps share the same keys.

        Returns the snapshot directory.
        """
        snap_dir = run_dir / SNAPSHOT_DIRNAME
        snap_dir.mkdir(parents=True, exist_ok=True)

        # Per-slot files (preserve original ``content`` exactly).
        for slot_id, content in effective_prompts.contents.items():
            ext = _ext_for_slot(slot_id)
            (snap_dir / f"{slot_id}{ext}").write_text(content, encoding="utf-8")

        # Combined manifest.
        combined = {
            "schema_version": effective_prompts.schema_version,
            "slot_to_variant": dict(effective_prompts.slot_to_variant),
            "contents": dict(effective_prompts.contents),
        }
        manifest = snap_dir / EFFECTIVE_PROMPTS_FILE
        tmp = manifest.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(combined, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(manifest)
        return snap_dir

    @staticmethod
    def read_effective_prompts(run_dir: Path) -> dict[str, Any]:
        """Read the ``effective_prompts.json`` manifest. Raises on miss."""
        target = run_dir / SNAPSHOT_DIRNAME / EFFECTIVE_PROMPTS_FILE
        return json.loads(target.read_text(encoding="utf-8"))


__all__ = [
    "EFFECTIVE_PROMPTS_FILE",
    "LOG_FILENAME",
    "METADATA_FILENAME",
    "OutputStore",
    "SNAPSHOT_DIRNAME",
    "is_valid_run_id",
    "make_run_id",
]
