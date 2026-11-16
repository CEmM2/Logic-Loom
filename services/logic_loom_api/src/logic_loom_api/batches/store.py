"""On-disk JSON store for editable source batches.

The store is rooted at a single file ``<akms_workspace>/batches.json``
holding an envelope::

    {
      "schema_version": 1,
      "batches": [ <EditableBatch>, ... ]
    }

The on-disk JSON is the source of truth — the in-memory :class:`BatchStore`
is just a thin Pydantic envelope. We deliberately avoid an index dir
(``batches/<id>.json``) for now: the dataset is small (tens to a few
hundred batches), atomic writes are easier on a single file, and the
legacy ``generation_plan.md`` bridge (:mod:`.compat`) round-trips against the
complete set.

Threading: NOT thread-safe. Callers wrap mutations at the route layer if
concurrency matters. The FastAPI single-process default makes this
sufficient for the tester release.

Slug generation (``slugify_title``) is stable: same title in -> same id
out. Collisions are resolved by appending ``-2``, ``-3``, ... so the picker
adapter never loses track of which on-disk record maps to which batch.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import SCHEMA_VERSION, EditableBatch

logger = logging.getLogger(__name__)

BATCHES_FILENAME = "batches.json"
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify_title(value: str) -> str:
    """Return a stable lowercase-hyphenated slug for ``value``.

    Empty / all-punctuation inputs return ``""`` so the caller can either
    re-prompt the user or fall back to a synthetic id. The store treats
    empty-id writes as :class:`ValueError`.

    Stability contract: ``slugify_title(t) == slugify_title(t)`` for any
    ``t`` — the function is pure and deterministic.
    """
    if not value:
        return ""
    s = _SLUG_NON_ALNUM.sub("-", value.strip().lower()).strip("-")
    return s


class BatchStore(BaseModel):
    """In-memory + on-disk envelope around a list of :class:`EditableBatch`.

    Construction does not touch the disk — use :meth:`load` to read or
    :meth:`save` to write. ``__init__`` semantics let route code build a
    store from a request body (e.g. the import endpoint) without hitting
    the filesystem.

    The class is a Pydantic model so the round-trip
    ``BatchStore.model_validate_json(store.model_dump_json())`` is the
    identity transformation (modulo the ordered fields). This is the
    behavioural surface the round-trip tests assert on.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    batches: list[EditableBatch] = Field(default_factory=list)

    # ----------------------------- query helpers -----------------------------

    def get(self, batch_id: str) -> EditableBatch | None:
        """Return the batch with the given id, or :class:`None` if absent."""
        for b in self.batches:
            if b.id == batch_id:
                return b
        return None

    def index_of(self, batch_id: str) -> int:
        """Return the position of the batch with ``batch_id`` or -1 if absent."""
        for i, b in enumerate(self.batches):
            if b.id == batch_id:
                return i
        return -1

    def ids(self) -> list[str]:
        """Return the batch ids in their stored order."""
        return [b.id for b in self.batches]

    # ------------------------------- mutators -------------------------------

    def add(self, batch: EditableBatch) -> EditableBatch:
        """Append ``batch`` to the store. Raises :class:`ValueError` if the
        id is already present (caller should mint a unique id via
        :meth:`new_id_from_title` first).
        """
        if not batch.id:
            raise ValueError("EditableBatch.id is empty; mint one before add()")
        if self.get(batch.id) is not None:
            raise ValueError(f"batch id already present: {batch.id!r}")
        self.batches.append(batch)
        return batch

    def replace(self, batch: EditableBatch) -> EditableBatch:
        """Replace the existing batch with the same id. Raises if absent."""
        i = self.index_of(batch.id)
        if i < 0:
            raise ValueError(f"batch id not found: {batch.id!r}")
        self.batches[i] = batch
        return batch

    def remove(self, batch_id: str) -> EditableBatch:
        """Remove and return the batch with ``batch_id``. Raises if absent."""
        i = self.index_of(batch_id)
        if i < 0:
            raise ValueError(f"batch id not found: {batch_id!r}")
        return self.batches.pop(i)

    # ------------------------------ id minting ------------------------------

    def new_id_from_title(self, title: str) -> str:
        """Return a fresh, store-unique id derived from ``title``.

        If the title slug collides with an existing id, append ``-2``,
        ``-3``, ... until a free id is found. Empty/punctuation-only
        titles raise :class:`ValueError` so the caller can surface a
        user-actionable error instead of writing an empty-id batch.

        Determinism: same title against the same set of existing ids
        always yields the same id — no random UUIDs, no timestamps.
        """
        base = slugify_title(title)
        if not base:
            raise ValueError(f"title does not produce a usable slug: {title!r}")
        existing = set(self.ids())
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"

    # ---------------------------- (de)serialise -----------------------------

    @classmethod
    def from_json(cls, text: str) -> "BatchStore":
        """Parse a JSON envelope back into a :class:`BatchStore`.

        Raises :class:`ValidationError` on malformed JSON or schema drift.
        """
        return cls.model_validate_json(text)

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialise the store as JSON. Default indent matches the on-disk
        format so a freshly-saved file is human-diffable.
        """
        return self.model_dump_json(indent=indent)

    # ----------------------------- filesystem ------------------------------

    @classmethod
    def load(cls, root: Path) -> "BatchStore":
        """Load the store from ``root/batches.json``.

        Missing file -> empty store (the first-run UX). Malformed file ->
        :class:`ValidationError` propagates; we deliberately do NOT swallow
        the error because a corrupted batches.json is a user-actionable
        condition (renaming it triggers the empty-store path on next load).
        """
        path = cls._path_for(root)
        try:
            if not path.exists():
                return cls()
            text = path.read_text(encoding="utf-8")
        except PermissionError as exc:
            # macOS TCC — treat as empty store so list endpoints do not 500.
            logger.warning(
                "batches.json unreadable at %s (%s); returning empty store. "
                "Move akms_workspace out of ~/Documents or grant Full Disk Access.",
                path,
                exc,
            )
            return cls()
        except OSError as exc:
            logger.warning("batches.json read failed at %s: %s", path, exc)
            raise
        return cls.from_json(text)

    def save(self, root: Path) -> Path:
        """Atomically write the store to ``root/batches.json``.

        Uses a temp file in the same directory + ``os.replace`` for atomicity
        on POSIX and Windows. Returns the resolved on-disk path.
        """
        root = Path(root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(root)
        # Same-dir tempfile so the atomic rename works across filesystems.
        fd, tmp_name = tempfile.mkstemp(
            prefix=".batches-", suffix=".json.tmp", dir=str(root)
        )
        tmp_path = Path(tmp_name)
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(self.to_json())
                f.write("\n")
            tmp_path.replace(path)
        except Exception:
            # Best-effort cleanup if the rename never happened.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return path

    @staticmethod
    def _path_for(root: Path) -> Path:
        return Path(root).expanduser() / BATCHES_FILENAME


def load_store(root: Path) -> BatchStore:
    """Module-level convenience for callers that prefer a function call."""
    return BatchStore.load(root)


def iter_batches(store: BatchStore) -> Iterable[EditableBatch]:
    """Module-level convenience for callers that prefer a function call."""
    return iter(store.batches)
