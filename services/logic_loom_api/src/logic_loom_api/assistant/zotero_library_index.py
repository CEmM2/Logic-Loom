"""FTS5 index over the full Zotero library (backs the Zotero picker).

The existing ``research_index/fts.py`` only indexes assistant artefacts
(``kind="zotero"`` is per-batch). To power the picker we need a
separate FTS5 store covering *every* Zotero item, regardless of whether
it has been pulled into an assistant batch yet.

Source of truth: the Better-BibTeX JSON export configured in the
zsum config (``bbt_json``). The index is rebuilt automatically when
the BBT file mtime is newer than the sqlite mtime; callers can also
force a rebuild via :meth:`ZoteroLibraryIndex.build`.

The index file lives at
``<assistant_workspace>/index/zotero_library_fts.sqlite`` so it shares
its lifecycle with the rest of the assistant indices.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..config import Settings

INDEX_FILENAME = "zotero_library_fts.sqlite"


def _expand(p: str) -> Path:
    return Path(p).expanduser()


# ---------------------------------------------------------------------------
# BBT JSON parsing
# ---------------------------------------------------------------------------


@dataclass
class ZoteroLibraryItem:
    """One row pulled from the BBT JSON export.

    Mirrors the subset of fields the picker UI needs. Extra BBT fields
    are intentionally ignored: we are not trying to reproduce the full
    Zotero schema here, just a search-friendly surface.
    """

    citekey: str
    title: str
    authors: list[str]
    year: int | None
    venue: str
    abstract: str
    doi: str
    url: str
    has_pdf: bool
    tags: list[str]
    collection_keys: list[str]


def _parse_authors(creators: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in creators:
        if c.get("creatorType") != "author":
            continue
        last = c.get("lastName", "").strip()
        first = c.get("firstName", "").strip()
        if last and first:
            out.append(f"{last}, {first}")
        elif last:
            out.append(last)
        elif c.get("name"):
            out.append(str(c["name"]))
    return out


def _parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    import re

    m = re.search(r"\b(19|20)\d{2}\b", str(date_str))
    return int(m.group()) if m else None


def _has_pdf(attachments: list[dict[str, Any]]) -> bool:
    for att in attachments:
        path = att.get("path", "")
        if isinstance(path, str) and path.lower().endswith(".pdf"):
            return True
    return False


def _iter_bbt_items(bbt_path: Path) -> Iterable[ZoteroLibraryItem]:
    """Yield :class:`ZoteroLibraryItem` rows from a BBT JSON file.

    Stops yielding silently if the file is unreadable or malformed —
    callers see an empty library and degrade-gracefully like the rest
    of the API (no Zotero, no picker results, but no crash).
    """
    try:
        with bbt_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    items = data if isinstance(data, list) else data.get("items", [])
    if not isinstance(items, list):
        return
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        citekey = str(raw_item.get("citationKey", "")).strip()
        if not citekey:
            continue
        yield ZoteroLibraryItem(
            citekey=citekey,
            title=str(raw_item.get("title", "")),
            authors=_parse_authors(raw_item.get("creators", []) or []),
            year=_parse_year(raw_item.get("date")),
            venue=str(
                raw_item.get("publicationTitle")
                or raw_item.get("proceedingsTitle")
                or raw_item.get("bookTitle")
                or ""
            ),
            abstract=str(raw_item.get("abstractNote", "")),
            doi=str(raw_item.get("DOI", "")),
            url=str(raw_item.get("url", "")),
            has_pdf=_has_pdf(raw_item.get("attachments", []) or []),
            tags=[
                str(t.get("tag", ""))
                for t in (raw_item.get("tags", []) or [])
                if isinstance(t, dict) and t.get("tag")
            ],
            collection_keys=[
                str(k)
                for k in (raw_item.get("collections", []) or [])
                if isinstance(k, (str, int))
            ],
        )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_bbt_json_path(settings: Settings) -> Path | None:
    """Return the BBT JSON path configured in the zsum config.

    Returns ``None`` when zsum.config.yaml is missing, malformed, or
    has no ``bbt_json`` key. The picker treats this as "no Zotero
    library available" rather than an error.
    """
    cfg_path = settings.effective_zsum_config_path()
    try:
        raw = cfg_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    bbt_value = parsed.get("bbt_json")
    if not isinstance(bbt_value, str) or not bbt_value.strip():
        return None
    return _expand(bbt_value)


def _index_path(settings: Settings) -> Path:
    return settings.assistant_workspace_path() / "index" / INDEX_FILENAME


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


class ZoteroLibraryIndex:
    """FTS5 sqlite index over the full Zotero library.

    Lifecycle:

    * :meth:`build` reads the BBT JSON and writes the table.
    * :meth:`ensure_fresh` rebuilds when the BBT file is newer than the
      index sqlite or when the sqlite does not exist.
    * :meth:`search` runs an FTS5 ``MATCH`` query and joins back to the
      ``items`` table for the metadata the picker UI needs.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # -- Connection management -----------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._ensure_schema(conn)
        self._conn = conn
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                citekey         TEXT PRIMARY KEY,
                title           TEXT NOT NULL DEFAULT '',
                authors_text    TEXT NOT NULL DEFAULT '',
                year            INTEGER,
                venue           TEXT NOT NULL DEFAULT '',
                abstract        TEXT NOT NULL DEFAULT '',
                doi             TEXT NOT NULL DEFAULT '',
                url             TEXT NOT NULL DEFAULT '',
                has_pdf         INTEGER NOT NULL DEFAULT 0,
                tags_text       TEXT NOT NULL DEFAULT '',
                collections_csv TEXT NOT NULL DEFAULT ''
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS items_fts
                USING fts5(
                    citekey,
                    title,
                    authors_text,
                    abstract,
                    venue,
                    tags_text,
                    content='items',
                    content_rowid='rowid',
                    tokenize='unicode61'
                );

            CREATE TRIGGER IF NOT EXISTS items_ai
                AFTER INSERT ON items BEGIN
                    INSERT INTO items_fts(rowid, citekey, title, authors_text, abstract, venue, tags_text)
                    VALUES (new.rowid, new.citekey, new.title, new.authors_text,
                            new.abstract, new.venue, new.tags_text);
                END;

            CREATE TRIGGER IF NOT EXISTS items_ad
                AFTER DELETE ON items BEGIN
                    INSERT INTO items_fts(items_fts, rowid, citekey, title, authors_text, abstract, venue, tags_text)
                    VALUES ('delete', old.rowid, old.citekey, old.title, old.authors_text,
                            old.abstract, old.venue, old.tags_text);
                END;

            CREATE TRIGGER IF NOT EXISTS items_au
                AFTER UPDATE ON items BEGIN
                    INSERT INTO items_fts(items_fts, rowid, citekey, title, authors_text, abstract, venue, tags_text)
                    VALUES ('delete', old.rowid, old.citekey, old.title, old.authors_text,
                            old.abstract, old.venue, old.tags_text);
                    INSERT INTO items_fts(rowid, citekey, title, authors_text, abstract, venue, tags_text)
                    VALUES (new.rowid, new.citekey, new.title, new.authors_text,
                            new.abstract, new.venue, new.tags_text);
                END;

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.commit()

    # -- Build / refresh -----------------------------------------------

    def build(self, bbt_path: Path) -> int:
        """Wipe the table and rebuild it from ``bbt_path``.

        Returns the number of items indexed. Always records the source
        path + mtime in the ``meta`` table so :meth:`ensure_fresh` can
        decide whether a rebuild is needed.
        """
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                conn.execute("DELETE FROM items")
                count = 0
                for item in _iter_bbt_items(bbt_path):
                    conn.execute(
                        """
                        INSERT INTO items (
                            citekey, title, authors_text, year, venue,
                            abstract, doi, url, has_pdf, tags_text,
                            collections_csv
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(citekey) DO UPDATE SET
                            title=excluded.title,
                            authors_text=excluded.authors_text,
                            year=excluded.year,
                            venue=excluded.venue,
                            abstract=excluded.abstract,
                            doi=excluded.doi,
                            url=excluded.url,
                            has_pdf=excluded.has_pdf,
                            tags_text=excluded.tags_text,
                            collections_csv=excluded.collections_csv
                        """,
                        (
                            item.citekey,
                            item.title,
                            "; ".join(item.authors),
                            item.year,
                            item.venue,
                            item.abstract,
                            item.doi,
                            item.url,
                            1 if item.has_pdf else 0,
                            "; ".join(item.tags),
                            ",".join(item.collection_keys),
                        ),
                    )
                    count += 1
                try:
                    src_mtime = bbt_path.stat().st_mtime
                except OSError:
                    src_mtime = 0.0
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('bbt_path', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(bbt_path),),
                )
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('bbt_mtime', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(src_mtime),),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return count

    def ensure_fresh(self, bbt_path: Path) -> int | None:
        """Rebuild when stale, return new count; return ``None`` when no-op."""
        try:
            src_mtime = bbt_path.stat().st_mtime
        except OSError:
            return None
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'bbt_mtime'"
            ).fetchone()
        prev_mtime = float(row["value"]) if row else 0.0
        prev_path = ""
        with self._lock:
            row2 = self._connect().execute(
                "SELECT value FROM meta WHERE key = 'bbt_path'"
            ).fetchone()
            if row2:
                prev_path = row2["value"]
        if prev_mtime >= src_mtime and prev_path == str(bbt_path):
            return None
        return self.build(bbt_path)

    # -- Read surface --------------------------------------------------

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            count = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
            mtime_row = conn.execute(
                "SELECT value FROM meta WHERE key = 'bbt_mtime'"
            ).fetchone()
            path_row = conn.execute(
                "SELECT value FROM meta WHERE key = 'bbt_path'"
            ).fetchone()
        return {
            "count": int(count),
            "bbt_path": path_row["value"] if path_row else None,
            "bbt_mtime": float(mtime_row["value"]) if mtime_row else None,
        }

    def search(
        self,
        *,
        query: str = "",
        collection_key: str | None = None,
        limit: int = 50,
    ) -> list[ZoteroLibraryItem]:
        """Return up to ``limit`` items matching ``query``.

        Empty query → newest-first by year (a stable default rather than
        an arbitrary FTS order). Collection filter applies post-FTS
        because the collection set lives in ``items.collections_csv``,
        not in the FTS table.
        """
        q = (query or "").strip()
        limit = max(1, min(limit, 500))
        with self._lock:
            conn = self._connect()
            if q:
                rows = conn.execute(
                    """
                    SELECT i.*
                    FROM items_fts f
                    JOIN items i ON i.rowid = f.rowid
                    WHERE items_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (_to_fts_query(q), limit * 4),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM items
                    ORDER BY year DESC, citekey ASC
                    LIMIT ?
                    """,
                    (limit * 4,),
                ).fetchall()

        out: list[ZoteroLibraryItem] = []
        for row in rows:
            if collection_key:
                csv = row["collections_csv"] or ""
                if collection_key not in csv.split(","):
                    continue
            out.append(_row_to_item(row))
            if len(out) >= limit:
                break
        return out

    def get(self, citekey: str) -> ZoteroLibraryItem | None:
        with self._lock:
            row = self._connect().execute(
                "SELECT * FROM items WHERE citekey = ?",
                (citekey,),
            ).fetchone()
        return _row_to_item(row) if row else None


def _to_fts_query(q: str) -> str:
    """Quote each token for FTS5 prefix matching.

    User queries are arbitrary text — a bare ``MATCH ?`` would let
    punctuation crash the parser. We split on whitespace, drop FTS
    operators (so ``"OR"`` typed by a human searches the literal word),
    and append ``*`` for cheap prefix matching.
    """
    cleaned: list[str] = []
    for token in q.split():
        token = token.strip()
        if not token:
            continue
        token = "".join(ch for ch in token if ch.isalnum() or ch in "-_")
        if not token:
            continue
        cleaned.append(f'"{token}"*')
    return " ".join(cleaned) if cleaned else '""'


def _row_to_item(row: sqlite3.Row) -> ZoteroLibraryItem:
    authors_text = (row["authors_text"] or "").strip()
    authors = [a.strip() for a in authors_text.split("; ") if a.strip()]
    tags_text = (row["tags_text"] or "").strip()
    tags = [t.strip() for t in tags_text.split("; ") if t.strip()]
    csv = (row["collections_csv"] or "").strip()
    cols = [c for c in csv.split(",") if c]
    return ZoteroLibraryItem(
        citekey=row["citekey"],
        title=row["title"] or "",
        authors=authors,
        year=row["year"],
        venue=row["venue"] or "",
        abstract=row["abstract"] or "",
        doi=row["doi"] or "",
        url=row["url"] or "",
        has_pdf=bool(row["has_pdf"]),
        tags=tags,
        collection_keys=cols,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def open_index(settings: Settings) -> ZoteroLibraryIndex:
    """Return a :class:`ZoteroLibraryIndex` for the active workspace."""
    return ZoteroLibraryIndex(_index_path(settings))


def ensure_built(settings: Settings) -> tuple[ZoteroLibraryIndex, dict[str, Any]]:
    """Open the index and refresh it from BBT when stale.

    Returns ``(index, status)`` where ``status`` carries a ``built`` int
    when a rebuild fired, the resolved bbt_path, and a ``reason`` string
    when the library is unavailable. The picker route serialises
    ``status`` into the search envelope so the UI can show a hint when
    the library is empty for predictable reasons.
    """
    idx = open_index(settings)
    bbt = resolve_bbt_json_path(settings)
    if bbt is None:
        return idx, {"reason": "bbt-path-unset", "bbt_path": None, "built": 0}
    if not bbt.exists():
        return idx, {"reason": "bbt-file-missing", "bbt_path": str(bbt), "built": 0}
    built = idx.ensure_fresh(bbt)
    return idx, {"reason": None, "bbt_path": str(bbt), "built": built or 0}


__all__ = [
    "INDEX_FILENAME",
    "ZoteroLibraryIndex",
    "ZoteroLibraryItem",
    "ensure_built",
    "open_index",
    "resolve_bbt_json_path",
]
