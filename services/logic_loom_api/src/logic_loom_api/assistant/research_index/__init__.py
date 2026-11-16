"""Research-index sub-package.

Module layout:

* :mod:`schema` — :class:`IndexableRecord` Pydantic model + the
  closed-set ``IndexKind`` literal (seven kinds: zotero, abstract,
  zsum_card, zsum_digest, assistant_output, akms_node, run_metadata).
* :mod:`ingest` — one adapter function per kind, reading from the
  canonical workspace location and producing :class:`IndexableRecord`
  instances. Dispatch table :data:`.ingest.ADAPTERS` keys every kind
  to its adapter for the rebuild walker.
* :mod:`fts` — SQLite + FTS5 backend
  (``<assistant_workspace>/index/fts.sqlite``). :class:`.fts.FtsIndex`
  exposes ``upsert`` / ``delete`` / ``search`` / ``count_by_kind`` /
  ``total``. ``content_hash`` drives reindex skipping.
* :mod:`lifecycle` — on_change hooks + rebuild route +
  500ms debounce.

The :class:`ResearchIndexService` re-export preserves the service-factory
shape (``get_assistant_services(settings).research_index``).
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from .fts import FtsIndex
from .lifecycle import IndexLifecycle, get_lifecycle_for
from .schema import IndexableRecord


class ResearchIndexService:
    """Workspace-wide research search service.

    :meth:`search` runs against the FTS5 backend; :meth:`rebuild` is
    wired to the lifecycle hooks + rebuild route.

    The class owns one :class:`FtsIndex` per ``Settings`` instance; the
    index is opened lazily on first call so constructing the service
    never touches disk.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._index: FtsIndex | None = None
        self._lifecycle: IndexLifecycle | None = None

    def _get_index(self) -> FtsIndex:
        """Lazy-open the underlying :class:`FtsIndex`.

        First call creates the workspace ``index/`` directory (if
        missing) and the sqlite database (with FTS5 tables); subsequent
        calls return the cached instance.
        """
        if self._index is None:
            self._index = FtsIndex(self._settings.assistant_workspace_path())
        return self._index

    def _get_lifecycle(self) -> IndexLifecycle:
        """Return the process-wide lifecycle singleton for these settings.

        Delegates to
        :func:`assistant.research_index.lifecycle.get_lifecycle_for` so
        the batch-write hook and the HTTP search/rebuild routes share
        the same instance (same debounce buffer, same persisted
        ``last_rebuild_at``).
        """
        if self._lifecycle is None:
            self._lifecycle = get_lifecycle_for(self._settings)
        return self._lifecycle

    def search(
        self,
        query: str,
        *,
        kind: str | list[str] | None = None,
        limit: int = 50,
        **_filters: Any,
    ) -> list[IndexableRecord]:
        """Search the index. See :meth:`FtsIndex.search` for semantics."""
        filters: dict[str, Any] = {}
        if kind is not None:
            filters["kind"] = kind
        return self._get_index().search(query, filters=filters, limit=limit)

    def rebuild(self) -> dict[str, int]:
        """Full-rebuild the index. See :meth:`IndexLifecycle.rebuild`."""
        return self._get_lifecycle().rebuild()

    def status(self) -> dict[str, Any]:
        """Return the index status. See :meth:`IndexLifecycle.status`."""
        return self._get_lifecycle().status()


__all__ = [
    "FtsIndex",
    "IndexLifecycle",
    "IndexableRecord",
    "ResearchIndexService",
]
