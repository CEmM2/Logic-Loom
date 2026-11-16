"""Indexable artefact schema.

Defines the unified record type stored in the research-memory index.
The seven kinds form a closed set — adding a new kind is a deliberate
schema change (new ingest adapter, status counts, and UI filters):

* ``zotero`` — a :class:`BatchSourceItem` of ``kind="zotero"`` attached
  to a :class:`SourceBatch`.
* ``abstract`` — a discovered paper from the assistant discovery cache
  (OpenAlex / Semantic Scholar / merged result).
* ``zsum_card`` — a published Zotero paper-card markdown
  (``<vault>/Papers/@<citekey>.md``).
* ``zsum_digest`` — a published collection-digest markdown
  (``<vault>/Collections/<name>.md``).
* ``assistant_output`` — the rendered primary output of an Assistant
  workflow run (the most-recently-modified ``*.md`` at the run-dir
  root, per :func:`routes.assistant_outputs._find_primary_output`).
* ``akms_node`` — an AKMS knowledge-graph node markdown
  (``<effective_akms_global_vault>/**/*.md``).
* ``run_metadata`` — the ``metadata.json`` reproducibility artefact at
  the root of an Assistant run dir.

``content_hash`` is sha256 of a canonical projection of the record's
identity + content fields (``kind`` + ``title`` + ``body``). The hash
is the change-signal driver for incremental indexing — FtsIndex.upsert
no-ops when the on-disk row's hash matches the new record's hash.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Closed-set indexable artefact kinds. Order is documentation only;
#: callers use the literal type for static typing.
IndexKind = Literal[
    "zotero",
    "abstract",
    "zsum_card",
    "zsum_digest",
    "assistant_output",
    "akms_node",
    "run_metadata",
]

#: Tuple form of :data:`IndexKind`. Useful for runtime iteration (e.g.
#: parametrised tests, dispatch tables). Kept in sync with the Literal
#: above by inspection — adding a kind to one without the other is a
#: scaffold-time bug.
INDEX_KINDS: tuple[str, ...] = (
    "zotero",
    "abstract",
    "zsum_card",
    "zsum_digest",
    "assistant_output",
    "akms_node",
    "run_metadata",
)


def compute_content_hash(*, kind: str, title: str, body: str) -> str:
    """Return the sha256 hex digest used as :attr:`IndexableRecord.content_hash`.

    The hash is over a single canonical projection ``"<kind>\\n<title>\\n<body>"``
    so the same artefact always produces the same hash regardless of when
    or where the record is constructed. Excluding ``source_path`` /
    ``batch_id`` / ``workflow_id`` / ``run_id`` / timestamps from the hash
    is intentional: a paper card whose body is unchanged shouldn't
    invalidate the index just because it was moved on disk.
    """
    payload = f"{kind}\n{title}\n{body}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class IndexableRecord(BaseModel):
    """A record persisted to the research-memory index.

    Every adapter in :mod:`.ingest` produces instances of this model.
    :class:`FtsIndex` stores them in a ``records`` table mirrored
    by an FTS5 virtual table on ``(title, body)``.

    Field semantics:

    * ``id`` — globally unique record id. Adapters derive it from the
      source artefact (e.g. ``zotero:<batch_id>:<source_item_id>``,
      ``akms_node:<filename>``); the exact format is adapter-specific but
      every adapter guarantees stability across reruns on the same input.
    * ``kind`` — closed-set literal (see :data:`IndexKind`).
    * ``title`` — short human-readable label. Indexed in FTS.
    * ``body`` — long-form searchable text. Indexed in FTS.
    * ``source_path`` — absolute path to the source artefact on disk
      (or a stable URI for non-file sources). Stored verbatim, not
      indexed — purely for UI deep-linking.
    * ``batch_id`` / ``workflow_id`` / ``run_id`` — optional foreign-key
      style hints. Set when the source artefact belongs to one of those
      contexts; ``None`` otherwise.
    * ``created_at`` / ``updated_at`` — ISO-8601 strings carried from
      the source artefact when available. Both are advisory; they are
      not used to drive incremental indexing (that's ``content_hash``'s
      job).
    * ``content_hash`` — sha256 hex digest computed by
      :func:`compute_content_hash`. Adapters either call the helper
      directly or pass ``content_hash=compute_content_hash(...)``
      explicitly; never construct the hash by hand.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: IndexKind
    title: str
    body: str = ""
    source_path: str = ""
    batch_id: str | None = None
    workflow_id: str | None = None
    run_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    content_hash: str = Field(default="")

    def with_computed_hash(self) -> "IndexableRecord":
        """Return a copy with ``content_hash`` populated from the canonical fields.

        Convenience for adapters that prefer to construct the model then
        let the helper fill in the hash, rather than threading
        :func:`compute_content_hash` through every adapter call site.
        Returns the same instance when ``content_hash`` is already set
        to keep the model immutable from the caller's perspective.
        """
        if self.content_hash:
            return self
        return self.model_copy(
            update={
                "content_hash": compute_content_hash(
                    kind=self.kind, title=self.title, body=self.body
                )
            }
        )


__all__ = [
    "INDEX_KINDS",
    "IndexKind",
    "IndexableRecord",
    "compute_content_hash",
]
