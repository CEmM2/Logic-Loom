"""SQLite + FTS5 backend.

Persists :class:`.schema.IndexableRecord` instances to a single
``fts.sqlite`` database under ``<assistant_workspace>/index/``. The
database carries two tables:

* ``records`` — full record payload (one row per record id).
* ``records_fts`` — FTS5 virtual table over ``(title, body)`` with the
  ``unicode61`` tokenizer for the actual search index.

The ``records`` table stores the canonical row; ``records_fts``
mirrors ``(title, body)`` so FTS5 can MATCH efficiently. A pair of
triggers keeps the FTS table in sync with any INSERT/UPDATE/DELETE on
``records``. The triggers fire automatically — callers always go
through :meth:`FtsIndex.upsert` / :meth:`FtsIndex.delete`.

Reindex skipping: :meth:`FtsIndex.upsert` reads the existing row's
``content_hash`` and short-circuits when the new record's hash
matches. This makes re-running ingestion on an unchanged workspace
essentially free — important because the full-rebuild route may be
called from the FE on every Assistant tab open.

Thread safety: ``sqlite3.Connection`` is not safe across threads
without ``check_same_thread=False``. We open the connection with the
flag and serialise writes via the SQLite per-connection lock; readers
acquire a shared lock automatically. The lifecycle hook batches
writes via the debounce buffer so the per-connection lock contention
is minimal.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .schema import INDEX_KINDS, IndexableRecord

logger = logging.getLogger(__name__)

#: Default sub-path under the assistant workspace.
INDEX_DIRNAME = "index"
INDEX_FILENAME = "fts.sqlite"


def _index_path(assistant_workspace: Path) -> Path:
    """Resolve the canonical FTS sqlite path."""
    return assistant_workspace / INDEX_DIRNAME / INDEX_FILENAME


class FtsIndex:
    """SQLite + FTS5 backend over :class:`IndexableRecord`.

    Single-process, single-file. Opened lazily; the first
    :meth:`upsert` / :meth:`search` call creates the file and tables.

    Construction:

        idx = FtsIndex(<assistant_workspace>)
        idx.upsert(record)
        idx.search("plasticity", filters={"kind": "akms_node"})
        idx.delete("zotero:b1:s1")

    Pass an in-memory path (``Path(":memory:")``) for tests that don't
    need persistence — the schema is identical.
    """

    def __init__(
        self,
        assistant_workspace: Path,
        *,
        db_path: Path | None = None,
    ) -> None:
        """Build an FtsIndex over ``<assistant_workspace>/index/fts.sqlite``.

        ``db_path`` overrides the resolved location for tests; pass
        ``Path(":memory:")`` to skip filesystem persistence entirely.
        """
        self._workspace = Path(assistant_workspace)
        self._db_path = db_path if db_path is not None else _index_path(self._workspace)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open (or return the cached) sqlite3 connection.

        Creates the parent directory + DDL on first call. Idempotent
        across calls within the same FtsIndex instance.
        """
        if self._conn is not None:
            return self._conn
        target = str(self._db_path)
        if target != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the lifecycle's debounce flush thread
        # can share this connection. We serialise writes via the
        # instance _lock.
        conn = sqlite3.connect(target, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Standard pragmas: WAL mode for concurrent reads + foreign keys
        # off (we don't model relationships across tables). FTS5 ships
        # in the stdlib sqlite3 build on Python 3.12.
        if target != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_schema(conn)
        self._conn = conn
        return conn

    def close(self) -> None:
        """Close the underlying connection. Safe to call multiple times."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        """Create the records + records_fts tables + sync triggers.

        Idempotent: every statement uses ``IF NOT EXISTS``.
        """
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                id            TEXT PRIMARY KEY,
                kind          TEXT NOT NULL,
                title         TEXT NOT NULL,
                body          TEXT NOT NULL DEFAULT '',
                source_path   TEXT NOT NULL DEFAULT '',
                batch_id      TEXT,
                workflow_id   TEXT,
                run_id        TEXT,
                created_at    TEXT,
                updated_at    TEXT,
                content_hash  TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind);

            CREATE VIRTUAL TABLE IF NOT EXISTS records_fts
                USING fts5(
                    title,
                    body,
                    content='records',
                    content_rowid='rowid',
                    tokenize='unicode61'
                );

            -- Sync triggers keep records_fts in lockstep with records.
            CREATE TRIGGER IF NOT EXISTS records_ai
                AFTER INSERT ON records BEGIN
                    INSERT INTO records_fts(rowid, title, body)
                    VALUES (new.rowid, new.title, new.body);
                END;

            CREATE TRIGGER IF NOT EXISTS records_ad
                AFTER DELETE ON records BEGIN
                    INSERT INTO records_fts(records_fts, rowid, title, body)
                    VALUES('delete', old.rowid, old.title, old.body);
                END;

            CREATE TRIGGER IF NOT EXISTS records_au
                AFTER UPDATE ON records BEGIN
                    INSERT INTO records_fts(records_fts, rowid, title, body)
                    VALUES('delete', old.rowid, old.title, old.body);
                    INSERT INTO records_fts(rowid, title, body)
                    VALUES (new.rowid, new.title, new.body);
                END;
            """
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Write surface
    # ------------------------------------------------------------------

    def upsert(self, record: IndexableRecord) -> bool:
        """Insert or update a record.

        Returns ``True`` when the on-disk row was created or replaced;
        ``False`` when the no-op short-circuit fired (existing row's
        ``content_hash`` matched the new record).

        The hash check is the change-signal driver. The lifecycle
        hooks call :meth:`upsert` unconditionally — the no-op makes
        bulk-import passes cheap.
        """
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                "SELECT content_hash FROM records WHERE id = ?",
                (record.id,),
            )
            row = cursor.fetchone()
            new_hash = record.content_hash or record.with_computed_hash().content_hash
            if row is not None and row["content_hash"] == new_hash:
                return False
            payload = record.model_dump()
            payload["content_hash"] = new_hash
            # Pydantic's `id` field maps directly to our PK column.
            conn.execute(
                """
                INSERT INTO records
                    (id, kind, title, body, source_path, batch_id, workflow_id,
                     run_id, created_at, updated_at, content_hash)
                VALUES
                    (:id, :kind, :title, :body, :source_path, :batch_id,
                     :workflow_id, :run_id, :created_at, :updated_at,
                     :content_hash)
                ON CONFLICT(id) DO UPDATE SET
                    kind         = excluded.kind,
                    title        = excluded.title,
                    body         = excluded.body,
                    source_path  = excluded.source_path,
                    batch_id     = excluded.batch_id,
                    workflow_id  = excluded.workflow_id,
                    run_id       = excluded.run_id,
                    created_at   = excluded.created_at,
                    updated_at   = excluded.updated_at,
                    content_hash = excluded.content_hash
                """,
                payload,
            )
            conn.commit()
            return True

    def delete(self, record_id: str) -> bool:
        """Delete a record by id. Returns ``True`` when a row was removed."""
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                "DELETE FROM records WHERE id = ?", (record_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def upsert_many(self, records: Iterable[IndexableRecord]) -> tuple[int, int]:
        """Upsert many records. Returns ``(written, skipped)``."""
        written = 0
        skipped = 0
        for record in records:
            if self.upsert(record):
                written += 1
            else:
                skipped += 1
        return written, skipped

    def clear(self) -> int:
        """Delete every record. Returns the number of rows removed."""
        with self._lock:
            conn = self._connect()
            cursor = conn.execute("DELETE FROM records")
            conn.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Read surface
    # ------------------------------------------------------------------

    def get(self, record_id: str) -> IndexableRecord | None:
        """Return a single record by id, or ``None`` if absent."""
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                "SELECT * FROM records WHERE id = ?", (record_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_record(row)

    def search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[IndexableRecord]:
        """Full-text search across ``(title, body)``.

        Args:
            query: FTS5 MATCH expression. Bare-word queries work
                (``"plasticity"``); phrase queries use double-quotes
                (``'"finite strain"'``). Empty query returns an empty
                list (rather than every record).
            filters: Optional column equality filters. Currently
                supported keys: ``kind`` (single value or list of
                values). Unknown keys are silently ignored.
            limit: Maximum rows to return. The FTS5 ``ORDER BY rank``
                clause surfaces the most relevant matches first.

        Returns:
            A list of :class:`IndexableRecord`, ordered by FTS5 rank.
        """
        if not query.strip():
            return []
        with self._lock:
            conn = self._connect()
            kind_filter = _normalize_kind_filter(filters)
            sql_parts = [
                "SELECT r.* FROM records r",
                "JOIN records_fts f ON f.rowid = r.rowid",
                "WHERE records_fts MATCH ?",
            ]
            params: list[Any] = [query]
            if kind_filter is not None:
                placeholders = ",".join("?" for _ in kind_filter)
                sql_parts.append(f"AND r.kind IN ({placeholders})")
                params.extend(kind_filter)
            sql_parts.append("ORDER BY rank LIMIT ?")
            params.append(int(limit))
            cursor = conn.execute(" ".join(sql_parts), params)
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def count_by_kind(self) -> dict[str, int]:
        """Return per-kind row counts (every kind keyed, including zeros).

        Used by the ``GET /api/assistant/index/status`` endpoint to
        report the closed-set counts even when a kind has no records.
        """
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                "SELECT kind, COUNT(*) AS n FROM records GROUP BY kind"
            )
            seen = {row["kind"]: row["n"] for row in cursor.fetchall()}
            # Ensure every closed-set kind is keyed.
            return {kind: seen.get(kind, 0) for kind in INDEX_KINDS}

    def total(self) -> int:
        """Return the total record count across every kind."""
        with self._lock:
            conn = self._connect()
            cursor = conn.execute("SELECT COUNT(*) AS n FROM records")
            return int(cursor.fetchone()["n"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> IndexableRecord:
        """Materialise a record from a sqlite3 row."""
        return IndexableRecord(
            id=row["id"],
            kind=row["kind"],
            title=row["title"],
            body=row["body"],
            source_path=row["source_path"],
            batch_id=row["batch_id"],
            workflow_id=row["workflow_id"],
            run_id=row["run_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            content_hash=row["content_hash"],
        )


def _normalize_kind_filter(filters: dict[str, Any] | None) -> list[str] | None:
    """Reduce the ``kind`` filter to a list of strings (or None when absent).

    Accepts either a single string (``filters={"kind": "zotero"}``)
    or a list / tuple (``filters={"kind": ["zotero", "akms_node"]}``).
    Returns ``None`` when no filter applies so the caller skips the
    extra WHERE clause.
    """
    if not filters:
        return None
    value = filters.get("kind")
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    raise TypeError(
        f"filters['kind'] must be str or list/tuple of str, got {type(value).__name__}"
    )


__all__ = [
    "INDEX_DIRNAME",
    "INDEX_FILENAME",
    "FtsIndex",
]
