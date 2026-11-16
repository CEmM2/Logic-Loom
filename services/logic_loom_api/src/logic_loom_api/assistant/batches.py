"""Source-batch registry service.

Persists Assistant batches to ``<assistant_workspace>/batches.json`` and
implements eight actions:

  1. ``create(title, **kwargs)`` — empty batch.
  2. ``create_from_discovery(results, title)`` — seeded from a list of
     discovered papers (dicts or `DiscoveredPaper` objects; duck-typed on
     attribute / key access).
  3. ``create_from_zotero_collection(collection_key, *, title=None,
     citekeys=None)`` — seeded from a Zotero collection key. Takes an
     explicit ``citekeys`` list so callers can seed without the
     ``ZoteroCatalogMatcher`` present.
  4. ``add_sources(batch_id, items)`` — append source items.
  5. ``remove_sources(batch_id, ids)`` — drop matching source ids.
  6. ``reorder_sources(batch_id, ordering)`` — permute the source list.
  7. ``duplicate(batch_id)`` — clone under a fresh id.
  8. ``archive(batch_id)`` — set status to ``"archived"``.

Plus ``send_to_akms(batch_id)`` — projects the batch onto an
:class:`logic_loom_api.batches.models.EditableBatch` via
:meth:`SourceBatch.to_editable_batch` and persists it via the
AKMS-bundle :class:`logic_loom_api.batches.store.BatchStore`. Returns
``{akms_batch_id, akms_workspace_path}``.

On-disk schema (forward-compatible): the bootstrap seeds ``batches.json``
as ``{}``; the registry pins it to::

    {
      "schema_version": 1,
      "batches": [ <SourceBatch>, ... ]
    }

Legacy ``{}`` is read as an empty store. Writes always produce the
envelope above. Atomic writes use a same-dir tempfile + ``os.replace``
following the AKMS-bundle ``BatchStore.save`` pattern.

Threading: not thread-safe. Route handlers wrap mutations in
``get_state_lock()`` if concurrency matters (same pattern as the
AKMS-bundle batches routes).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field

from ..batches.store import BatchStore, slugify_title
from ..config import Settings
from .models import SCHEMA_VERSION, BatchSourceItem, SourceBatch

logger = logging.getLogger(__name__)

#: On-disk envelope schema version. Bumped when the registry format
#: changes incompatibly with prior versions.
ASSISTANT_BATCHES_SCHEMA_VERSION = SCHEMA_VERSION


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class _BatchesEnvelope(BaseModel):
    """On-disk envelope for ``batches.json``.

    The envelope is private to this module — callers interact with the
    service methods, not the envelope directly. Pydantic validation
    guarantees both fields exist and types are coerced on load.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = ASSISTANT_BATCHES_SCHEMA_VERSION
    batches: list[SourceBatch] = Field(default_factory=list)


def _new_source_item_id(existing: Iterable[str], hint: str | None = None) -> str:
    """Mint a unique source-item id.

    Stable + deterministic: same hint + same existing-set always yields
    the same id. If ``hint`` slugs cleanly use it, otherwise fall back
    to ``src-<n>`` where ``n`` is the smallest free index.
    """
    used = set(existing)
    if hint:
        slug = slugify_title(hint)
        if slug and slug not in used:
            return slug
        # Append numeric suffix.
        if slug:
            i = 2
            while f"{slug}-{i}" in used:
                i += 1
            return f"{slug}-{i}"
    i = 1
    while f"src-{i}" in used:
        i += 1
    return f"src-{i}"


def _coerce_source(
    raw: Any, existing_ids: set[str], default_kind: str = "discovered"
) -> BatchSourceItem:
    """Project a duck-typed source descriptor onto :class:`BatchSourceItem`.

    Accepts:
      * a :class:`BatchSourceItem` instance (returned as-is, after id
        de-duplication against ``existing_ids``);
      * a :class:`dict` — passed to ``BatchSourceItem.model_validate``;
      * any object with attribute access — fields are sniffed by name.

    Missing ``id`` is minted via :func:`_new_source_item_id` using the
    item title as the slug hint. Missing ``kind`` defaults to
    ``default_kind`` (``"discovered"`` for discovery seeds, callers
    override for other paths).
    """
    if isinstance(raw, BatchSourceItem):
        if raw.id and raw.id not in existing_ids:
            return raw
        new_id = _new_source_item_id(existing_ids, raw.title or raw.id)
        return raw.model_copy(update={"id": new_id})

    if isinstance(raw, dict):
        payload = dict(raw)
    else:
        # Attribute-bag duck typing — sniff a known field set.
        payload = {}
        for field in BatchSourceItem.model_fields:
            if hasattr(raw, field):
                payload[field] = getattr(raw, field)

    payload.setdefault("kind", default_kind)
    title = payload.get("title", "")
    if not payload.get("id"):
        payload["id"] = _new_source_item_id(existing_ids, title or None)
    elif payload["id"] in existing_ids:
        payload["id"] = _new_source_item_id(existing_ids, title or None)

    return BatchSourceItem.model_validate(payload)


class SourceBatchService:
    """Assistant source-batch registry.

    Construct with a :class:`Settings` instance — the registry resolves
    its on-disk file via ``settings.assistant_batches_path()`` on every
    method call, so tests can swap ``LOGIC_LOOM_ASSISTANT_WORKSPACE`` and
    the service follows.

    Methods mutate ``batches.json`` atomically; reads are uncached
    (matches the AKMS-bundle batches route style — keeps tests easy to
    isolate and stays correct under the "no in-process cache" model).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        on_batch_changed: Callable[[SourceBatch], None] | None = None,
        on_sources_deleted: Callable[[str, list[BatchSourceItem]], None] | None = None,
    ) -> None:
        """Bind the registry to ``settings``.

        ``on_batch_changed`` is an optional post-write callback fired
        after every mutation that produces a :class:`SourceBatch`. The
        factory in :mod:`assistant.services` wires this to the
        research-index lifecycle so newly-created or edited batches show
        up in Cmd-K search without a manual rebuild.

        ``on_sources_deleted`` is invoked with ``(batch_id, removed)``
        whenever sources are evicted (``remove_sources`` and ``delete``).
        The factory wires it to :meth:`IndexLifecycle.on_delete` so the
        FTS5 index drops the evicted records without waiting for a
        manual rebuild.

        Both callbacks run synchronously after the save; failures are
        logged at WARNING and swallowed so an indexer hiccup never
        blocks a batch write.
        """
        self._settings = settings
        self._on_batch_changed = on_batch_changed
        self._on_sources_deleted = on_sources_deleted

    def _notify_changed(self, batch: SourceBatch) -> None:
        """Best-effort post-write hook. Failures degrade gracefully."""
        if self._on_batch_changed is None:
            return
        try:
            self._on_batch_changed(batch)
        except Exception as exc:  # noqa: BLE001 — preserve degrade-gracefully
            logger.warning(
                "assistant.batches: post-write index hook failed: %s", exc
            )

    def _notify_sources_deleted(
        self, batch_id: str, removed: list[BatchSourceItem]
    ) -> None:
        """Best-effort eviction hook. Failures degrade gracefully."""
        if self._on_sources_deleted is None or not removed:
            return
        try:
            self._on_sources_deleted(batch_id, removed)
        except Exception as exc:  # noqa: BLE001 — preserve degrade-gracefully
            logger.warning(
                "assistant.batches: eviction index hook failed: %s", exc
            )

    # ------------------------------ persistence -----------------------------

    def _path(self) -> Path:
        return self._settings.assistant_batches_path()

    def _load(self) -> _BatchesEnvelope:
        """Read the envelope from disk.

        Missing file → empty envelope. Legacy ``{}`` seed (workspace bootstrap)
        is also read as empty. Malformed JSON propagates so a corrupt
        file is user-actionable.
        """
        path = self._path()
        try:
            if not path.exists():
                return _BatchesEnvelope()
            text = path.read_text(encoding="utf-8")
        except PermissionError as exc:
            # macOS TCC (Documents / Desktop / OneDrive) — degrade to empty so
            # the Assistant batches page stays usable instead of 500ing.
            logger.warning(
                "assistant batches.json unreadable at %s (%s); "
                "returning empty list. Move assistant_workspace / akms_workspace "
                "out of ~/Documents or grant Full Disk Access to the host app.",
                path,
                exc,
            )
            return _BatchesEnvelope()
        except OSError as exc:
            logger.warning("assistant batches.json read failed at %s: %s", path, exc)
            raise
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("assistant batches.json is malformed at %s", path)
            raise
        # Legacy seed: bootstrap writes `{}` (no schema_version, no batches).
        if not isinstance(data, dict) or "batches" not in data:
            return _BatchesEnvelope()
        return _BatchesEnvelope.model_validate(data)

    def _save(self, env: _BatchesEnvelope) -> None:
        """Atomically write the envelope to ``batches.json``.

        Same pattern as :class:`logic_loom_api.batches.store.BatchStore.save`:
        write to a same-dir tempfile, then ``os.replace`` for POSIX +
        Windows atomicity.
        """
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".batches-", suffix=".json.tmp", dir=str(path.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(env.model_dump_json(indent=2))
                f.write("\n")
            os.replace(tmp_path, path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ----------------------------- id minting ------------------------------

    def _new_batch_id(self, title: str, existing_ids: Iterable[str]) -> str:
        """Mint a fresh, store-unique batch id from ``title``.

        Mirrors :meth:`BatchStore.new_id_from_title` semantics so the
        Assistant and AKMS stores agree on slug rules.
        """
        base = slugify_title(title)
        if not base:
            base = "batch"
        used = set(existing_ids)
        if base not in used:
            return base
        i = 2
        while f"{base}-{i}" in used:
            i += 1
        return f"{base}-{i}"

    # ----------------------------- read accessors --------------------------

    def list_batches(self) -> list[SourceBatch]:
        """Return every batch on disk, in stored order."""
        return list(self._load().batches)

    def get(self, batch_id: str) -> SourceBatch | None:
        """Return the batch with ``batch_id`` or :class:`None` if absent."""
        for b in self._load().batches:
            if b.id == batch_id:
                return b
        return None

    # ----------------------------- create paths ----------------------------

    def create(
        self,
        title: str,
        *,
        description: str = "",
        sources: list[BatchSourceItem] | None = None,
        tags: list[str] | None = None,
        purpose: str | None = None,
        notebook_id: str | None = None,
        default_workflow_id: str | None = None,
        prompt_profile_id: str | None = None,
        prompt_overrides: dict[str, str] | None = None,
    ) -> SourceBatch:
        """Create an empty (or pre-seeded) batch and persist it."""
        env = self._load()
        batch_id = self._new_batch_id(title, (b.id for b in env.batches))
        now = _utc_now_iso()
        batch = SourceBatch(
            id=batch_id,
            title=title,
            description=description,
            tags=list(tags or []),
            purpose=purpose,
            sources=list(sources or []),
            notebook_id=notebook_id,
            default_workflow_id=default_workflow_id,
            prompt_profile_id=prompt_profile_id,
            prompt_overrides=dict(prompt_overrides or {}),
            created_at=now,
            updated_at=now,
        )
        env.batches.append(batch)
        self._save(env)
        self._notify_changed(batch)
        return batch

    def create_from_discovery(
        self,
        results: Iterable[Any],
        title: str,
    ) -> SourceBatch:
        """Seed a batch from a discovery result iterable.

        Each result is coerced to :class:`BatchSourceItem` with
        ``kind="discovered"``. This method duck-types so it works
        against any paper shape (dict, the ``DiscoveredPaper`` dataclass,
        pydantic model).
        """
        seen_ids: set[str] = set()
        items: list[BatchSourceItem] = []
        for raw in results:
            item = _coerce_source(raw, seen_ids, default_kind="discovered")
            seen_ids.add(item.id)
            items.append(item)
        return self.create(title=title, sources=items)

    def create_from_zotero_collection(
        self,
        collection_key: str,
        *,
        title: str | None = None,
        citekeys: Iterable[str] | None = None,
    ) -> SourceBatch:
        """Seed a batch from a Zotero collection key.

        Accepts an explicit ``citekeys`` list so the registry can be
        exercised standalone; resolving the collection → citekeys mapping
        via :class:`ZoteroCatalogMatcher` is not wired here.

        Each citekey becomes a :class:`BatchSourceItem` with
        ``kind="zotero"``.
        """
        seen_ids: set[str] = set()
        items: list[BatchSourceItem] = []
        for citekey in citekeys or []:
            item_id = _new_source_item_id(seen_ids, hint=citekey)
            seen_ids.add(item_id)
            items.append(
                BatchSourceItem(
                    id=item_id,
                    kind="zotero",
                    citekey=citekey,
                    title=citekey,
                )
            )
        batch_title = title or f"Zotero collection {collection_key}"
        batch = self.create(
            title=batch_title,
            description=f"Seeded from Zotero collection {collection_key}",
            sources=items,
            tags=["zotero-collection"],
        )
        return batch

    # ----------------------------- mutators --------------------------------

    def _replace(self, env: _BatchesEnvelope, batch: SourceBatch) -> None:
        """In-place replace ``batch`` in the envelope."""
        for i, existing in enumerate(env.batches):
            if existing.id == batch.id:
                env.batches[i] = batch
                return
        raise ValueError(f"batch id not found: {batch.id!r}")

    def add_sources(
        self, batch_id: str, items: Iterable[Any]
    ) -> SourceBatch:
        """Append ``items`` to the batch's source list."""
        env = self._load()
        batch = self._require(env, batch_id)
        existing_ids = {s.id for s in batch.sources}
        for raw in items:
            item = _coerce_source(raw, existing_ids)
            existing_ids.add(item.id)
            batch.sources.append(item)
        batch.updated_at = _utc_now_iso()
        self._replace(env, batch)
        self._save(env)
        self._notify_changed(batch)
        return batch

    def remove_sources(
        self, batch_id: str, ids: Iterable[str]
    ) -> SourceBatch:
        """Drop sources whose ``id`` is in ``ids``."""
        env = self._load()
        batch = self._require(env, batch_id)
        drop = set(ids)
        removed = [s for s in batch.sources if s.id in drop]
        batch.sources = [s for s in batch.sources if s.id not in drop]
        batch.updated_at = _utc_now_iso()
        self._replace(env, batch)
        self._save(env)
        # Eviction first so the index drops the removed records, then
        # an upsert for the remaining batch state.
        self._notify_sources_deleted(batch.id, removed)
        self._notify_changed(batch)
        return batch

    def reorder_sources(
        self, batch_id: str, ordering: list[str]
    ) -> SourceBatch:
        """Permute the source list to match ``ordering``.

        ``ordering`` must be a permutation of the existing source ids;
        any id not present in the batch is rejected with :class:`ValueError`
        so callers can surface a 4xx instead of silently losing data.
        """
        env = self._load()
        batch = self._require(env, batch_id)
        by_id = {s.id: s for s in batch.sources}
        if set(ordering) != set(by_id.keys()):
            raise ValueError(
                "reorder ordering must be a permutation of existing source ids"
            )
        batch.sources = [by_id[i] for i in ordering]
        batch.updated_at = _utc_now_iso()
        self._replace(env, batch)
        self._save(env)
        self._notify_changed(batch)
        return batch

    def update(
        self,
        batch_id: str,
        **fields: Any,
    ) -> SourceBatch:
        """Patch arbitrary fields on a batch (excluding ``id`` / ``sources``).

        Used by the PATCH route. ``sources`` mutations go through
        the dedicated add/remove/reorder methods so the source-id
        invariants stay intact.
        """
        env = self._load()
        batch = self._require(env, batch_id)
        protected = {"id", "sources", "created_at", "schema_version"}
        for key, value in fields.items():
            if key in protected:
                continue
            if key in SourceBatch.model_fields:
                setattr(batch, key, value)
        batch.updated_at = _utc_now_iso()
        self._replace(env, batch)
        self._save(env)
        self._notify_changed(batch)
        return batch

    def duplicate(self, batch_id: str) -> SourceBatch:
        """Clone ``batch_id`` under a fresh id.

        The clone's ``title`` is suffixed with `" (copy)"`. ``created_at``
        and ``updated_at`` are reset to "now". Sources are deep-copied —
        the clone is fully independent of the source batch.
        """
        env = self._load()
        original = self._require(env, batch_id)
        new_title = f"{original.title} (copy)"
        new_id = self._new_batch_id(new_title, (b.id for b in env.batches))
        now = _utc_now_iso()
        clone_data = original.model_dump()
        clone_data["id"] = new_id
        clone_data["title"] = new_title
        clone_data["created_at"] = now
        clone_data["updated_at"] = now
        clone_data["status"] = "draft"
        clone = SourceBatch.model_validate(clone_data)
        # Deep-copy sources so future mutations on the clone don't touch
        # the original's source list.
        clone.sources = [s.model_copy(deep=True) for s in clone.sources]
        env.batches.append(clone)
        self._save(env)
        self._notify_changed(clone)
        return clone

    def archive(self, batch_id: str) -> SourceBatch:
        """Set ``status="archived"`` on the batch."""
        env = self._load()
        batch = self._require(env, batch_id)
        batch.status = "archived"
        batch.updated_at = _utc_now_iso()
        self._replace(env, batch)
        self._save(env)
        self._notify_changed(batch)
        return batch

    def delete(self, batch_id: str) -> SourceBatch:
        """Remove the batch from the registry. Returns the removed record.

        Fires the eviction hook with every source on the batch so the
        research-memory index drops the records for this batch's
        ``zotero:<batch_id>:<source_id>`` ids.
        """
        env = self._load()
        for i, batch in enumerate(env.batches):
            if batch.id == batch_id:
                removed = env.batches.pop(i)
                self._save(env)
                self._notify_sources_deleted(removed.id, list(removed.sources))
                return removed
        raise ValueError(f"batch id not found: {batch_id!r}")

    # ----------------------------- send to AKMS ----------------------------

    def send_to_akms(self, batch_id: str) -> dict[str, str]:
        """Project this batch onto the AKMS workspace.

        Steps:
          1. Load the Assistant batch.
          2. Build an :class:`EditableBatch` via
             :meth:`SourceBatch.to_editable_batch`.
          3. If an AKMS batch with the same id already exists (i.e. the
             user is re-sending the same Assistant batch after editing
             it), **overwrite** the existing entry so the user's latest
             ``default_workflow_id`` / ``prompt_profile_id`` /
             ``papers`` show up on the AKMS side. The earlier policy
             (mint a fresh slug) produced confusing duplicate batches
             where the original copy still showed "(none)" for the
             workflow.
          4. Persist via :meth:`BatchStore.save` rooted at
             ``settings.akms_workspace``.

        Returns ``{"akms_batch_id": str, "akms_workspace_path": str}``.
        The Assistant batch is not mutated — send-to-akms remains
        one-way and explicit.
        """
        batch = self.get(batch_id)
        if batch is None:
            raise ValueError(f"batch id not found: {batch_id!r}")

        akms_root = Path(self._settings.akms_workspace).expanduser()
        akms_root.mkdir(parents=True, exist_ok=True)
        store = BatchStore.load(akms_root)

        editable = batch.to_editable_batch()
        existing = store.get(editable.id)
        if existing is not None:
            # Re-send: replace the existing AKMS batch in-place so the
            # latest workflow / profile / source list propagates.
            store.replace(editable)
        else:
            store.add(editable)
        store.save(akms_root)
        return {
            "akms_batch_id": editable.id,
            "akms_workspace_path": str(akms_root),
        }

    # ------------------------------ helpers --------------------------------

    def _require(
        self, env: _BatchesEnvelope, batch_id: str
    ) -> SourceBatch:
        """Return the batch with ``batch_id`` or raise :class:`ValueError`."""
        for batch in env.batches:
            if batch.id == batch_id:
                return batch
        raise ValueError(f"batch id not found: {batch_id!r}")


__all__ = [
    "ASSISTANT_BATCHES_SCHEMA_VERSION",
    "SourceBatchService",
]
