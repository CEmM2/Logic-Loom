"""Ingestion adapters per artefact kind.

One adapter per :data:`.schema.IndexKind`. Each reads from its
canonical workspace location and yields :class:`IndexableRecord`
instances. The dispatch table :data:`ADAPTERS` keys each kind to its
adapter so :class:`.lifecycle.IndexLifecycle.rebuild` can walk the
full set without hard-coding the seven function names.

Each adapter follows the same shape:

* Accept :class:`Settings` so paths route through the resolver layer.
* Yield zero records when the canonical location is missing or empty
  (graceful degrade — partial workspaces are valid).
* Catch + log per-record errors at WARNING; raise on top-level
  configuration errors so the caller surfaces them.

The per-run prompt-snapshot filename is uniform — both the Assistant
runner and the shared ``OutputStore`` write ``effective_prompts.json``
(underscore). An earlier dash-vs-underscore split is resolved at the
writer side; readers no longer need to probe both spellings.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Iterator

from ...config import Settings
from ..models import BatchSourceItem, SourceBatch
from .schema import IndexKind, IndexableRecord, compute_content_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers (small + pure so they're easy to unit test)
# ---------------------------------------------------------------------------


def _safe_read_json(path: Path) -> dict | list | None:
    """Read JSON from ``path``; return ``None`` on missing or malformed input.

    Per-record failures are logged at WARNING and yield ``None`` so the
    adapter can move on to the next artefact without aborting the
    entire ingestion walk.
    """
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("research_index: failed to read %s: %s", path, exc)
        return None


def _safe_read_text(path: Path) -> str | None:
    """Read text from ``path``; return ``None`` on OS error."""
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("research_index: failed to read %s: %s", path, exc)
        return None


def _load_assistant_batches(settings: Settings) -> list[SourceBatch]:
    """Read the Assistant batch envelope from ``<workspace>/batches.json``.

    Returns an empty list on missing / malformed file so the zotero adapter
    can yield no records gracefully.
    """
    data = _safe_read_json(settings.assistant_batches_path())
    if not isinstance(data, dict):
        return []
    raw_batches = data.get("batches")
    if not isinstance(raw_batches, list):
        return []
    out: list[SourceBatch] = []
    for raw in raw_batches:
        try:
            out.append(SourceBatch.model_validate(raw))
        except Exception as exc:  # noqa: BLE001 — per-record degrade
            logger.warning("research_index: invalid SourceBatch entry: %s", exc)
    return out


def zotero_body_for(item: BatchSourceItem) -> str:
    """Build the FTS-indexed body string for a zotero-kind source item.

    Concatenates the available fields ((title), authors, year, doi,
    citekey) into a single newline-joined string. The title is
    intentionally repeated in the body so an exact title query still
    matches even when the FTS query phrases only the body column.

    Public so :mod:`assistant.services` can use it to build records for
    the post-write batch hook without duplicating the body shape.
    """
    parts: list[str] = [item.title]
    if item.authors:
        parts.append("Authors: " + "; ".join(item.authors))
    if item.year is not None:
        parts.append(f"Year: {item.year}")
    if item.doi:
        parts.append(f"DOI: {item.doi}")
    if item.citekey:
        parts.append(f"Citekey: {item.citekey}")
    return "\n".join(parts)


def _abstract_body_for(paper: dict) -> str:
    """Build the FTS-indexed body string for a discovered paper.

    The body includes title (echoed for the same reason as in
    :func:`zotero_body_for`), authors, year, and the abstract itself.
    """
    title = str(paper.get("title") or "")
    parts: list[str] = []
    if title:
        parts.append(title)
    authors = paper.get("authors") or []
    if isinstance(authors, list) and authors:
        parts.append("Authors: " + "; ".join(str(a) for a in authors))
    year = paper.get("year")
    if year is not None:
        parts.append(f"Year: {year}")
    abstract = paper.get("abstract")
    if isinstance(abstract, str) and abstract:
        parts.append(abstract)
    return "\n".join(parts)


def _record_from_text_file(
    *, kind: IndexKind, path: Path, id_prefix: str, **extras: object
) -> IndexableRecord | None:
    """Helper for adapters that index a markdown / text file by path.

    Constructs the :class:`IndexableRecord` with ``id_prefix:<filename>``
    as the id, the file's stem as the title, and the file contents as
    the body. Returns ``None`` if the file is unreadable.
    """
    body = _safe_read_text(path)
    if body is None:
        return None
    title = path.stem
    record_id = f"{id_prefix}:{path.name}"
    return IndexableRecord(
        id=record_id,
        kind=kind,
        title=title,
        body=body,
        source_path=str(path.resolve()),
        content_hash=compute_content_hash(kind=kind, title=title, body=body),
        **{k: v for k, v in extras.items() if v is not None},  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Per-kind adapters
# ---------------------------------------------------------------------------


def ingest_zotero(settings: Settings) -> Iterator[IndexableRecord]:
    """Yield one record per ``BatchSourceItem`` of ``kind="zotero"``.

    Source: ``<assistant_workspace>/batches.json``. Each yielded record
    carries ``batch_id`` so search results can deep-link to the batch.
    """
    for batch in _load_assistant_batches(settings):
        for item in batch.sources:
            if item.kind != "zotero":
                continue
            body = zotero_body_for(item)
            yield IndexableRecord(
                id=f"zotero:{batch.id}:{item.id}",
                kind="zotero",
                title=item.title,
                body=body,
                source_path=str(settings.assistant_batches_path().resolve()),
                batch_id=batch.id,
                created_at=batch.created_at,
                updated_at=batch.updated_at,
                content_hash=compute_content_hash(
                    kind="zotero", title=item.title, body=body
                ),
            )


def ingest_abstract(settings: Settings) -> Iterator[IndexableRecord]:
    """Yield one record per discovered paper in the discovery cache.

    Source: ``<assistant_workspace>/discovery_cache/*.json``. Each cache
    file holds an envelope keyed by ``papers``; we de-duplicate by the
    first stable identifier per paper (DOI → openalex_id →
    semantic_scholar_id → title) so the same paper appearing in
    multiple discovery responses is indexed once.
    """
    cache_dir = settings.assistant_workspace_path() / "discovery_cache"
    if not cache_dir.is_dir():
        return
    seen: set[str] = set()
    for cache_file in sorted(cache_dir.glob("*.json")):
        data = _safe_read_json(cache_file)
        if not isinstance(data, dict):
            continue
        papers = data.get("papers")
        if not isinstance(papers, list):
            continue
        for raw in papers:
            if not isinstance(raw, dict):
                continue
            ident = (
                str(raw.get("doi") or "")
                or str(raw.get("openalex_id") or "")
                or str(raw.get("semantic_scholar_id") or "")
                or str(raw.get("title") or "")
            )
            if not ident or ident in seen:
                continue
            seen.add(ident)
            title = str(raw.get("title") or ident)
            body = _abstract_body_for(raw)
            yield IndexableRecord(
                id=f"abstract:{ident}",
                kind="abstract",
                title=title,
                body=body,
                source_path=str(cache_file.resolve()),
                content_hash=compute_content_hash(
                    kind="abstract", title=title, body=body
                ),
            )


def ingest_zsum_card(settings: Settings) -> Iterator[IndexableRecord]:
    """Yield one record per published paper card.

    Source: ``<vault_path>/Papers/*.md`` (zsum publish layout). Returns
    nothing when ``vault_path`` is unset.
    """
    if settings.vault_path is None:
        return
    papers_dir = settings.vault_path.expanduser() / "Papers"
    if not papers_dir.is_dir():
        return
    for card_path in sorted(papers_dir.glob("*.md")):
        record = _record_from_text_file(
            kind="zsum_card", path=card_path, id_prefix="zsum_card"
        )
        if record is not None:
            yield record


def ingest_zsum_digest(settings: Settings) -> Iterator[IndexableRecord]:
    """Yield one record per published collection digest.

    Source: ``<vault_path>/Collections/*.md``. Returns nothing when
    ``vault_path`` is unset.
    """
    if settings.vault_path is None:
        return
    collections_dir = settings.vault_path.expanduser() / "Collections"
    if not collections_dir.is_dir():
        return
    for digest_path in sorted(collections_dir.glob("*.md")):
        record = _record_from_text_file(
            kind="zsum_digest", path=digest_path, id_prefix="zsum_digest"
        )
        if record is not None:
            yield record


def _iter_assistant_run_dirs(
    settings: Settings,
) -> Iterator[tuple[str, str, str, Path]]:
    """Yield ``(batch_id, workflow_id, run_id, run_dir)`` for every Assistant run.

    Source order, first non-empty wins:

      1. The global :class:`RunRegistry` (production: the runner registers
         every workflow run with the canonical ``log_path``, so the run
         directory is ``log_path.parent`` regardless of which workspace
         layout the user picked). Filtered to records with
         ``kind == "workflow"`` and an ``assistant.``-prefixed
         ``workflow_id``.
      2. Filesystem walk under ``<akms_workspace>/outputs/`` (legacy
         layout + test fixtures that bypass the registry).

    Falling back to the filesystem walk keeps the seven existing ingest
    adapter unit tests green — those tests materialise run directories
    directly without going through the runner.
    """
    # Registry path (production).
    try:
        from ...runs.registry import get_registry

        registry = get_registry()
    except (RuntimeError, ImportError):
        registry = None
    if registry is not None:
        records = registry.list(limit=10**6, kind="workflow")
        emitted_any = False
        for record in records:
            workflow_id = record.workflow_id or ""
            batch_id = record.batch_id or ""
            run_id = record.id
            if not workflow_id.startswith("assistant."):
                continue
            if not record.log_path:
                continue
            run_dir = Path(record.log_path).parent
            if not run_dir.is_dir():
                continue
            emitted_any = True
            yield batch_id, workflow_id, run_id, run_dir
        if emitted_any:
            return
        # Fall through to the filesystem walk only when the registry
        # had no matching records — covers fresh installs and tests
        # that pre-create directories without registering.

    # Filesystem walk (legacy + test fixtures).
    workspace = settings.akms_workspace.expanduser()
    outputs_root = workspace / "outputs"
    if not outputs_root.is_dir():
        return
    for batch_dir in sorted(outputs_root.iterdir()):
        if not batch_dir.is_dir():
            continue
        for workflow_dir in sorted(batch_dir.iterdir()):
            if not workflow_dir.is_dir():
                continue
            if not workflow_dir.name.startswith("assistant."):
                continue
            for run_dir in sorted(workflow_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                yield batch_dir.name, workflow_dir.name, run_dir.name, run_dir


def ingest_assistant_output(settings: Settings) -> Iterator[IndexableRecord]:
    """Yield one record per Assistant workflow run's primary output.

    Walks every Assistant workflow run via :func:`_iter_assistant_run_dirs`
    (registry → filesystem fallback) and indexes the most-recently-modified
    markdown at the run-dir root, matching the convention in
    :func:`routes.assistant_outputs._find_primary_output`.
    """
    for batch_id, workflow_id, run_id, run_dir in _iter_assistant_run_dirs(settings):
        primary = _pick_primary_md(run_dir)
        if primary is None:
            continue
        body = _safe_read_text(primary)
        if body is None:
            continue
        title = primary.stem
        yield IndexableRecord(
            id=f"assistant_output:{batch_id}:{workflow_id}:{run_id}",
            kind="assistant_output",
            title=title,
            body=body,
            source_path=str(primary.resolve()),
            batch_id=batch_id,
            workflow_id=workflow_id,
            run_id=run_id,
            content_hash=compute_content_hash(
                kind="assistant_output", title=title, body=body
            ),
        )


def _pick_primary_md(run_dir: Path) -> Path | None:
    """Return the most-recently-modified ``*.md`` at the run-dir root.

    Mirrors ``routes.assistant_outputs._find_primary_output``: excludes
    the ``prompt_snapshot/`` subdir's markdown files (those are
    snapshot per-slot dumps, not executor outputs).
    """
    candidates = [
        child
        for child in run_dir.iterdir()
        if child.is_file() and child.suffix.lower() == ".md"
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def ingest_akms_node(settings: Settings) -> Iterator[IndexableRecord]:
    """Yield one record per AKMS node markdown.

    Source: ``settings.effective_akms_global_vault()`` (the canonical
    AKMS knowledge vault — typically ``<akms_repo>/Packages/Nodes_Vault``).
    Walks recursively for ``.md`` files. Returns nothing when the vault
    is missing.
    """
    vault = settings.effective_akms_global_vault()
    if not vault.is_dir():
        return
    for node_path in sorted(vault.rglob("*.md")):
        record = _record_from_text_file(
            kind="akms_node",
            path=node_path,
            id_prefix="akms_node",
        )
        if record is not None:
            yield record


def ingest_run_metadata(settings: Settings) -> Iterator[IndexableRecord]:
    """Yield one record per Assistant run metadata file.

    Walks every Assistant workflow run via :func:`_iter_assistant_run_dirs`
    (registry → filesystem fallback) and indexes the ``metadata.json``
    at the run-dir root. The body is the raw JSON text so search hits
    include keys / values from the metadata payload.
    """
    for batch_id, workflow_id, run_id, run_dir in _iter_assistant_run_dirs(settings):
        metadata_path = run_dir / "metadata.json"
        body = _safe_read_text(metadata_path)
        if body is None:
            continue
        title = f"{workflow_id} / {run_id}"
        yield IndexableRecord(
            id=f"run_metadata:{batch_id}:{workflow_id}:{run_id}",
            kind="run_metadata",
            title=title,
            body=body,
            source_path=str(metadata_path.resolve()),
            batch_id=batch_id,
            workflow_id=workflow_id,
            run_id=run_id,
            content_hash=compute_content_hash(
                kind="run_metadata", title=title, body=body
            ),
        )


#: Dispatch table for the rebuild walker. Stable order so the per-kind
#: counts in ``GET /api/assistant/index/status`` come back in a
#: predictable shape.
ADAPTERS: dict[IndexKind, Callable[[Settings], Iterator[IndexableRecord]]] = {
    "zotero": ingest_zotero,
    "abstract": ingest_abstract,
    "zsum_card": ingest_zsum_card,
    "zsum_digest": ingest_zsum_digest,
    "assistant_output": ingest_assistant_output,
    "akms_node": ingest_akms_node,
    "run_metadata": ingest_run_metadata,
}


__all__ = [
    "ADAPTERS",
    "ingest_abstract",
    "ingest_akms_node",
    "ingest_assistant_output",
    "ingest_run_metadata",
    "ingest_zotero",
    "ingest_zsum_card",
    "ingest_zsum_digest",
    "zotero_body_for",
]
