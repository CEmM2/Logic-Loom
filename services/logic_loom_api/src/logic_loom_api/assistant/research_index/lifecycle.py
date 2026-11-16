"""Index lifecycle: incremental upserts + full rebuild.

Owns the wiring between artefact mutations and the FTS5 backend:

* :class:`IndexLifecycle` exposes ``on_change(record)`` and
  ``on_delete(record_id)`` callbacks that BatchStore + run output store
  + AKMS node store call when their backing data mutates. The two
  callbacks land in a debounce buffer; calls within a 500ms window
  collapse into a single flush so bulk-import passes don't thrash the
  indexer.
* :meth:`IndexLifecycle.rebuild` walks every workspace artefact via
  the seven :data:`.ingest.ADAPTERS` and upserts every record. The
  on-disk SQLite file is cleared first so stale records are evicted.
* :meth:`IndexLifecycle.status` returns the closed-set per-kind
  counts + the last-rebuild ISO-8601 timestamp.

Debounce timing: the buffer accumulates pending operations and starts
a single one-shot ``threading.Timer`` set to fire 500ms after the
most-recent enqueue. Subsequent ``on_change`` calls within that
window reset the timer (sliding-window semantics). On fire, the
buffer drains under the FtsIndex lock so writes serialise.

Thread safety: the buffer + timer are guarded by an instance lock.
The FtsIndex itself is thread-safe via its own lock. Tests
that need deterministic flush ordering call :meth:`flush` directly
instead of waiting on the timer.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from ...config import Settings
from .fts import INDEX_DIRNAME, FtsIndex
from .ingest import ADAPTERS
from .schema import INDEX_KINDS, IndexableRecord

logger = logging.getLogger(__name__)

#: Default debounce window — bulk imports collapse into one flush.
DEFAULT_DEBOUNCE_MS = 500

#: On-disk metadata file alongside ``fts.sqlite``. Persists
#: ``last_rebuild_at`` across sidecar restarts.
INDEX_META_FILENAME = "index.meta.json"


def _meta_path(assistant_workspace: Path) -> Path:
    """Resolve the canonical index-metadata path."""
    return assistant_workspace / INDEX_DIRNAME / INDEX_META_FILENAME


# Sentinel used in the pending-ops buffer to mark a deletion.
_DELETE_SENTINEL = object()


class IndexLifecycle:
    """Lifecycle facade for the research-memory index.

    Construct once per :class:`Settings` instance. Lifetime is the
    process lifetime; callers should not new it up per request.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        fts_index: FtsIndex | None = None,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._fts = fts_index or FtsIndex(settings.assistant_workspace_path())
        self._debounce_ms = int(debounce_ms)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._pending: dict[str, IndexableRecord | object] = {}
        self._timer: threading.Timer | None = None
        self._meta_path = _meta_path(settings.assistant_workspace_path())
        self._last_rebuild_at: str | None = self._load_last_rebuild_at()
        self._flush_count = 0  # debug counter for tests

    def _load_last_rebuild_at(self) -> str | None:
        """Read the persisted ``last_rebuild_at`` from disk, if any.

        Returns ``None`` on missing or malformed file (degrade-gracefully:
        a corrupt meta file is treated as "never rebuilt" so a fresh
        rebuild repopulates the timestamp).
        """
        try:
            if not self._meta_path.is_file():
                return None
            payload = json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "research_index: failed to read %s: %s", self._meta_path, exc
            )
            return None
        value = payload.get("last_rebuild_at") if isinstance(payload, dict) else None
        return value if isinstance(value, str) else None

    def _persist_last_rebuild_at(self, value: str) -> None:
        """Write ``last_rebuild_at`` to ``index.meta.json`` atomically.

        Best-effort: a write failure logs at WARNING and the in-memory
        value remains authoritative until the next rebuild. The sibling
        directory is created if missing.
        """
        try:
            self._meta_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._meta_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"last_rebuild_at": value}, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._meta_path)
        except OSError as exc:
            logger.warning(
                "research_index: failed to persist %s: %s",
                self._meta_path,
                exc,
            )

    # ------------------------------------------------------------------
    # Incremental hooks
    # ------------------------------------------------------------------

    def on_change(self, record: IndexableRecord) -> None:
        """Enqueue an upsert for the given record.

        Calls within the 500ms debounce window collapse into a single
        flush. The latest enqueue per record id wins (intra-window
        replays of the same record only schedule one upsert).
        """
        with self._lock:
            self._pending[record.id] = record
            self._reschedule_flush_locked()

    def on_delete(self, record_id: str) -> None:
        """Enqueue a delete for the given record id.

        Same debounce semantics as :meth:`on_change`. A delete that
        replaces an earlier change in the same window cancels the
        upsert (the buffer holds the sentinel instead).
        """
        with self._lock:
            self._pending[record_id] = _DELETE_SENTINEL
            self._reschedule_flush_locked()

    def _reschedule_flush_locked(self) -> None:
        """Replace any pending timer with a fresh one (assumes lock held).

        Sliding-window semantics: every enqueue extends the flush
        deadline by ``debounce_ms``. If callers stop enqueueing, the
        timer fires once and the buffer drains.
        """
        if self._timer is not None:
            self._timer.cancel()
        # Convert milliseconds → seconds (threading.Timer accepts float seconds).
        self._timer = threading.Timer(
            self._debounce_ms / 1000.0, self._timer_fire
        )
        self._timer.daemon = True
        self._timer.start()

    def _timer_fire(self) -> None:
        """Timer callback — drains the buffer under the lock."""
        try:
            self.flush()
        except Exception as exc:  # noqa: BLE001 — preserve daemon safety
            logger.warning("research_index: debounce flush failed: %s", exc)

    def flush(self) -> tuple[int, int]:
        """Drain the pending buffer immediately.

        Returns ``(upserts, deletes)`` counts. Safe to call manually
        in tests for deterministic flush ordering.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self._pending:
                return 0, 0
            pending = self._pending
            self._pending = {}
        # Drain outside the lock — FtsIndex has its own lock. We snapshot
        # the buffer above and operate on the snapshot here.
        upserts = 0
        deletes = 0
        for record_id, payload in pending.items():
            if payload is _DELETE_SENTINEL:
                if self._fts.delete(record_id):
                    deletes += 1
            else:
                if self._fts.upsert(payload):  # type: ignore[arg-type]
                    upserts += 1
        with self._lock:
            self._flush_count += 1
        return upserts, deletes

    @property
    def flush_count(self) -> int:
        """Number of times the buffer has drained (debug/test accessor)."""
        return self._flush_count

    @property
    def pending_size(self) -> int:
        """Current pending-buffer size (debug/test accessor)."""
        with self._lock:
            return len(self._pending)

    # ------------------------------------------------------------------
    # Full rebuild
    # ------------------------------------------------------------------

    def rebuild(self) -> dict[str, int]:
        """Walk every workspace artefact and rebuild the index.

        Drains the debounce buffer first, clears the on-disk records
        table, then re-ingests every kind via :data:`.ingest.ADAPTERS`.
        Returns the per-kind counts after the rebuild.

        This is the recovery path when the SQLite file is wiped or the
        hash table drifts. The route handler exposes it via
        ``POST /api/assistant/index/rebuild``.
        """
        # Drain any in-flight changes so they aren't applied after the clear.
        self.flush()
        self._fts.clear()
        counts: dict[str, int] = {kind: 0 for kind in INDEX_KINDS}
        for kind, adapter in ADAPTERS.items():
            for record in adapter(self._settings):
                if self._fts.upsert(record):
                    counts[kind] += 1
        self._last_rebuild_at = self._clock().isoformat()
        self._persist_last_rebuild_at(self._last_rebuild_at)
        return counts

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        kinds: list[str] | None = None,
        limit: int = 50,
    ) -> list[IndexableRecord]:
        """Run a full-text search against the FTS5 backend.

        Thin wrapper around :meth:`FtsIndex.search` that normalises the
        request shape used by ``POST /api/assistant/index/search`` and
        the FE workspace-search modal. An empty / missing ``kinds`` list
        means "all kinds"; an explicit list restricts the result set.
        """
        filters: dict[str, Any] = {}
        if kinds:
            filters["kind"] = list(kinds)
        return self._fts.search(query, filters=filters, limit=limit)

    def status(self) -> dict[str, Any]:
        """Return ``{counts: {kind: int}, total, last_rebuild_at}``.

        Used by ``GET /api/assistant/index/status``. ``last_rebuild_at``
        survives sidecar restarts via ``index.meta.json``; it is ``None``
        only when the index has never been rebuilt.
        """
        counts = self._fts.count_by_kind()
        return {
            "counts": counts,
            "total": self._fts.total(),
            "last_rebuild_at": self._last_rebuild_at,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Cancel any pending timer + close the FTS index.

        Idempotent. Tests call this in fixtures to avoid leaking
        debounce-timer threads.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._fts.close()


#: Process-wide lifecycle registry keyed by ``id(settings)``. Both the
#: HTTP-layer dep provider (:func:`routes.assistant_index.get_index_lifecycle`)
#: and the data-layer batch-write hook resolve through this registry so
#: every caller targets the same debounce buffer and on-disk
#: ``index.meta.json`` for a given :class:`Settings` instance.
_LIFECYCLE_REGISTRY: dict[int, IndexLifecycle] = {}


def get_lifecycle_for(settings: Settings) -> IndexLifecycle:
    """Return the process-wide :class:`IndexLifecycle` for ``settings``.

    Constructs the lifecycle on first call; subsequent calls with the
    same ``Settings`` id return the cached instance. Use
    :func:`reset_lifecycle_registry` (typically from a test fixture +
    ``get_settings.cache_clear()``) to drop the cache between cases.
    """
    key = id(settings)
    if key not in _LIFECYCLE_REGISTRY:
        _LIFECYCLE_REGISTRY[key] = IndexLifecycle(settings)
    return _LIFECYCLE_REGISTRY[key]


def reset_lifecycle_registry() -> None:
    """Drop every cached lifecycle. Closes each before evicting."""
    for lifecycle in _LIFECYCLE_REGISTRY.values():
        try:
            lifecycle.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
    _LIFECYCLE_REGISTRY.clear()


__all__ = [
    "DEFAULT_DEBOUNCE_MS",
    "INDEX_META_FILENAME",
    "IndexLifecycle",
    "get_lifecycle_for",
    "reset_lifecycle_registry",
]
