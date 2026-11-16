"""Read-only adapter over ``akms-records`` stores.

``akms-records`` (EXPERIMENTAL upstream, AKMS 0.4) keeps typed records — an
AKMS-fixed envelope around a payload described by a user-registered JSON
Schema — as an append-only, hash-chained history of plain files. Logic-Loom
only BROWSES them: list and filter records, show one record with its event
history and its schema, and draw the graph the records form.

Rules this module keeps:

* **Optional package.** ``akms_records`` is imported lazily. When it is missing
  :func:`records_available` says so and every other entry point raises
  :class:`RecordsAdapterError` with code ``records_unavailable``; the sidecar
  and the rest of the app are unaffected.
* **Read-only.** Only the package's read API is used: ``RecordStore.open``,
  ``iter_records``, ``get``, ``history`` and ``SchemaRegistry.refs``/``get``.
  Stores are opened with ``index=False`` and nothing here calls ``recall``,
  ``wake``, ``doctor`` or ``citing_summaries`` — those open (and may create)
  the derived SQLite index under the store root. Search here is a plain scan.
* **Explicit stores.** A store is visible only because it is listed in
  ``[[records_stores]]``; nothing is discovered by scanning. The store root is
  resolved by the package's own ``store_root``.

The record graph
----------------
Payloads are user-defined, so the graph is built only from envelope fields
every record has, never from payload keys:

* ``superseded_by`` (replay state) and ``supersedes`` source refs → a
  ``superseded_by`` edge from the old record to its successor;
* a source ref whose ``ref`` is a record id or ``<record_id>@<revision>`` →
  ``cites`` (kind ``record``, what ``consolidate`` summaries write) or
  ``references`` (any other kind) from the citing record to the cited one;
* the reserved ``promoted-to`` ref → ``promoted_to`` edge to the AKMS node;
* every other source ref (a URL, a file, a DOI …) → ``source_ref`` edge to an
  ``external`` node, when ``include_external`` is set.

A cited record that is not in the store becomes a ``missing_record`` node, so
a dangling reference is visible rather than silently dropped.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import RecordsStore

#: The record-id format akms-records documents (``ids.RECORD_ID_RE``), repeated
#: so graph building does not depend on a non-``__all__`` module.
_RECORD_ID = r"rec-[0-9]{8}-[0-9a-hjkmnp-tv-z]{8}"
_RECORD_REF_RE = re.compile(rf"({_RECORD_ID})(?:@([1-9][0-9]{{0,5}}))?")

PROMOTED_KIND = "promoted-to"
SUPERSEDES_KIND = "supersedes"
CITATION_KIND = "record"

DEFAULT_GRAPH_LIMIT = 500
MAX_GRAPH_LIMIT = 5000


class RecordsAdapterError(Exception):
    """A typed failure the routes turn into an error envelope."""

    def __init__(self, message: str, *, code: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ── availability ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Availability:
    installed: bool
    version: str | None
    reason: str | None


def records_available() -> Availability:
    """Whether ``akms_records`` imports, and its version."""
    try:
        import akms_records
    except ImportError as exc:
        return Availability(
            installed=False,
            version=None,
            reason=(
                "akms-records is not installed (install the 'records' extra: "
                f"uv sync --all-packages --extra records). Import error: {exc}"
            ),
        )
    return Availability(
        installed=True, version=str(getattr(akms_records, "__version__", "")), reason=None
    )


def _package() -> Any:
    try:
        import akms_records
    except ImportError as exc:
        raise RecordsAdapterError(
            "akms-records is not installed (install the 'records' extra).",
            code="records_unavailable",
            status=503,
        ) from exc
    return akms_records


def _wrap(exc: Exception) -> RecordsAdapterError:
    """Translate an akms-records error into a typed adapter error."""
    pkg = _package()
    code = str(getattr(exc, "code", "") or "records_error")
    status = 400
    if isinstance(exc, pkg.NotFound):
        status = 404
    elif isinstance(exc, pkg.ScopeViolation):
        status = 404
    elif isinstance(exc, pkg.ReplayError):
        status = 422
    return RecordsAdapterError(str(exc), code=code, status=status)


# ── store access ────────────────────────────────────────────────────────────


def store_root(cfg: RecordsStore) -> Path:
    """The store root ``cfg`` names, resolved by the package."""
    pkg = _package()
    try:
        scope = pkg.Scope.parse(cfg.scope)
        return Path(
            pkg.store_root(
                scope,
                repo=cfg.repo.expanduser() if cfg.repo is not None else None,
                home=cfg.home.expanduser() if cfg.home is not None else None,
            )
        )
    except pkg.RecordsError as exc:
        raise RecordsAdapterError(
            f"records store {cfg.id!r}: {exc}", code="store_config", status=400
        ) from exc


def _open(cfg: RecordsStore) -> Any:
    pkg = _package()
    root = store_root(cfg)
    try:
        # index=False: reads here never touch the derived SQLite index.
        return pkg.RecordStore(root, pkg.Scope.parse(cfg.scope), index=False)
    except pkg.RecordsError as exc:
        raise _wrap(exc) from exc


def _scan(store: Any) -> tuple[list[Any], list[dict[str, Any]]]:
    """Every record of the store's scope, plus records that failed to replay."""
    pkg = _package()
    records: list[Any] = []
    errors: list[dict[str, Any]] = []
    for item in store.iter_records():
        if isinstance(item, pkg.ReplayError):
            errors.append(
                {
                    "record_id": getattr(item, "record_id", None),
                    "code": getattr(item, "replay_code", None) or getattr(item, "code", "replay_error"),
                    "message": str(item),
                }
            )
        else:
            records.append(item)
    return records, errors


def _summary_row(record: Any) -> dict[str, Any]:
    state = record.state_json()
    return {
        "record_id": record.record_id,
        "revision": record.revision,
        "schema_ref": record.schema_ref,
        "summary": record.summary,
        "tags": list(record.tags),
        "recorded_at": record.recorded_at,
        "updated_at": state.get("updated_at"),
        "origin": dict(record.origin),
        "sensitivity": record.sensitivity,
        "review_state": record.review_state,
        "lifecycle": record.lifecycle,
        "superseded_by": state.get("superseded_by"),
        "promoted_to": state.get("promoted_to"),
        "source_ref_count": len(record.source_refs),
    }


def describe_store(cfg: RecordsStore) -> dict[str, Any]:
    """Store metadata for the store picker. Never raises for a missing root."""
    out: dict[str, Any] = {
        "id": cfg.id,
        "scope": cfg.scope,
        "root": None,
        "exists": False,
        "record_count": None,
        "schema_count": None,
        "replay_errors": 0,
        "error": None,
    }
    try:
        root = store_root(cfg)
        out["root"] = str(root)
        out["exists"] = root.is_dir()
        if out["exists"]:
            store = _open(cfg)
            records, errors = _scan(store)
            out["record_count"] = len(records)
            out["replay_errors"] = len(errors)
            out["schema_count"] = len(store.schemas.refs())
    except RecordsAdapterError as exc:
        out["error"] = {"code": exc.code, "message": str(exc)}
    return out


# ── records ─────────────────────────────────────────────────────────────────


def _matches_text(record: Any, needle: str) -> bool:
    haystack = " ".join(
        [
            record.record_id,
            record.summary,
            " ".join(record.tags),
            json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
        ]
    ).lower()
    return all(word in haystack for word in needle.lower().split())


def list_records(
    cfg: RecordsStore,
    *,
    schema: str | None = None,
    tag: str | None = None,
    lifecycle: str | None = None,
    review_state: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """Records of the store, newest first, filtered on envelope fields.

    ``schema`` matches a full ref (``decision/v1``) or a bare name
    (``decision``, any version). ``q`` is a case-insensitive scan over the id,
    summary, tags and payload text — no index is consulted.
    """
    root = store_root(cfg)
    if not root.is_dir():
        return {"records": [], "total": 0, "replay_errors": [], "facets": _facets([])}
    store = _open(cfg)
    try:
        records, errors = _scan(store)
    except Exception as exc:  # noqa: BLE001 — package errors become envelopes
        raise _wrap(exc) from exc
    facets = _facets(records)

    def keep(record: Any) -> bool:
        if schema:
            name = record.schema_ref.split("/", 1)[0]
            if record.schema_ref != schema and name != schema:
                return False
        if tag and tag not in record.tags:
            return False
        if lifecycle and record.lifecycle != lifecycle:
            return False
        if review_state and record.review_state != review_state:
            return False
        if q and q.strip() and not _matches_text(record, q):
            return False
        return True

    selected = sorted(
        (r for r in records if keep(r)),
        key=lambda r: (r.recorded_at, r.record_id),
        reverse=True,
    )
    page = selected[offset : offset + limit]
    return {
        "records": [_summary_row(r) for r in page],
        "total": len(selected),
        "replay_errors": errors,
        "facets": facets,
    }


def _facets(records: list[Any]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {
        "schema_ref": {},
        "tags": {},
        "lifecycle": {},
        "review_state": {},
    }
    for record in records:
        for key, values in (
            ("schema_ref", [record.schema_ref]),
            ("tags", record.tags),
            ("lifecycle", [record.lifecycle]),
            ("review_state", [record.review_state]),
        ):
            for value in values:
                out[key][value] = out[key].get(value, 0) + 1
    return out


def get_record(cfg: RecordsStore, record_id: str) -> dict[str, Any]:
    """One record: envelope + payload, replay state, events, schema, neighbours."""
    if not re.fullmatch(_RECORD_ID, record_id):
        raise RecordsAdapterError(
            f"{record_id!r} is not a record id", code="invalid_record_id", status=400
        )
    pkg = _package()
    store = _open(cfg)
    try:
        record = store.get(record_id)
        history = store.history(record_id)
    except pkg.RecordsError as exc:
        raise _wrap(exc) from exc

    schema: dict[str, Any] | None = None
    schema_error: dict[str, Any] | None = None
    try:
        schema = store.schemas.get(record.schema_ref).to_json(include_schema=True)
    except pkg.RecordsError as exc:
        schema_error = {"code": getattr(exc, "code", "records_error"), "message": str(exc)}

    graph = record_graph(cfg, focus=record_id, depth=1, include_external=True)
    return {
        "record": record.envelope_json(),
        "state": record.state_json(),
        "history": [_event_view(event) for event in history],
        "schema": schema,
        "schema_error": schema_error,
        "neighbours": graph,
    }


def _event_view(event: Any) -> dict[str, Any]:
    """An event file, minus nothing: events are the store's public history."""
    return event if isinstance(event, dict) else {"event": event}


def list_schemas(cfg: RecordsStore) -> dict[str, Any]:
    """Registered schemas (each with its JSON Schema) and how many records use it."""
    pkg = _package()
    root = store_root(cfg)
    if not root.is_dir():
        return {"schemas": []}
    store = _open(cfg)
    records, _ = _scan(store)
    usage: dict[str, int] = {}
    for record in records:
        usage[record.schema_ref] = usage.get(record.schema_ref, 0) + 1
    out = []
    for ref in store.schemas.refs():
        entry: dict[str, Any] = {"schema_ref": ref, "record_count": usage.get(ref, 0)}
        try:
            entry.update(store.schemas.get(ref).to_json(include_schema=True))
            entry["error"] = None
        except pkg.RecordsError as exc:
            entry["error"] = {"code": getattr(exc, "code", "records_error"), "message": str(exc)}
        out.append(entry)
    return {"schemas": out}


# ── graph ───────────────────────────────────────────────────────────────────


def _record_node(record: Any) -> dict[str, Any]:
    return {
        "id": record.record_id,
        "kind": "record",
        "label": record.summary,
        "schema_ref": record.schema_ref,
        "lifecycle": record.lifecycle,
        "review_state": record.review_state,
        "tags": list(record.tags),
        "recorded_at": record.recorded_at,
    }


def build_graph(
    records: list[Any], *, include_external: bool = True
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Nodes and edges (keyed by id) from the records' envelopes alone."""
    nodes: dict[str, dict[str, Any]] = {r.record_id: _record_node(r) for r in records}
    edges: dict[str, dict[str, Any]] = {}

    def add_edge(source: str, target: str, type_: str, label: str | None = None, **extra: Any) -> None:
        edge_id = f"{type_}:{source}->{target}"
        if label and type_ in {"references", "source_ref"}:
            edge_id += f":{label}"
        edges.setdefault(
            edge_id,
            {"id": edge_id, "source": source, "target": target, "type": type_, "label": label or type_, **extra},
        )

    def ensure_record(record_id: str) -> None:
        if record_id not in nodes:
            nodes[record_id] = {
                "id": record_id,
                "kind": "missing_record",
                "label": f"{record_id} (not in this store)",
            }

    for record in records:
        rid = record.record_id
        superseded_by = record.superseded_by
        if superseded_by:
            ensure_record(superseded_by)
            add_edge(rid, superseded_by, "superseded_by")
        for ref in record.source_refs:
            kind = str(ref.get("kind", ""))
            text = str(ref.get("ref", ""))
            if kind == PROMOTED_KIND:
                node_id, _, revision = text.rpartition("@")
                node_id = node_id or text
                target = f"akms:{node_id}"
                nodes.setdefault(target, {"id": target, "kind": "akms_node", "label": node_id})
                add_edge(rid, target, "promoted_to", revision=revision or None)
                continue
            match = _RECORD_REF_RE.fullmatch(text)
            if match:
                cited = match.group(1)
                revision = int(match.group(2)) if match.group(2) else None
                if cited == rid:
                    continue
                ensure_record(cited)
                if kind == SUPERSEDES_KIND:
                    add_edge(cited, rid, "superseded_by")
                elif kind == CITATION_KIND:
                    add_edge(rid, cited, "cites", "cites", revision=revision)
                else:
                    add_edge(rid, cited, "references", kind, revision=revision)
                continue
            if include_external and text:
                target = f"ext:{kind}:{text}"
                nodes.setdefault(
                    target,
                    {"id": target, "kind": "external", "label": text, "ref_kind": kind, "note": ref.get("note")},
                )
                add_edge(rid, target, "source_ref", kind)
    return nodes, edges


def _neighbourhood(
    edges: dict[str, dict[str, Any]], focus: str, depth: int
) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for edge in edges.values():
        adjacency.setdefault(edge["source"], set()).add(edge["target"])
        adjacency.setdefault(edge["target"], set()).add(edge["source"])
    seen = {focus}
    queue = deque([(focus, 0)])
    while queue:
        node, distance = queue.popleft()
        if distance >= depth:
            continue
        for neighbour in adjacency.get(node, ()):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append((neighbour, distance + 1))
    return seen


def record_graph(
    cfg: RecordsStore,
    *,
    focus: str | None = None,
    depth: int = 2,
    include_external: bool = True,
    schema: str | None = None,
    limit: int = DEFAULT_GRAPH_LIMIT,
) -> dict[str, Any]:
    """The record graph of a store, optionally the neighbourhood of one record.

    ``schema`` keeps only records of that schema (ref or bare name) plus what
    they point at. ``limit`` caps the node count; ``truncated`` says when it
    bit, so a big store is never silently partial.
    """
    limit = max(1, min(limit, MAX_GRAPH_LIMIT))
    root = store_root(cfg)
    if not root.is_dir():
        return {"nodes": [], "edges": [], "truncated": False, "total_nodes": 0, "focus": focus}
    store = _open(cfg)
    records, _ = _scan(store)
    if schema:
        records = [
            r for r in records if r.schema_ref == schema or r.schema_ref.split("/", 1)[0] == schema
        ]
    nodes, edges = build_graph(records, include_external=include_external)
    if focus is not None:
        if focus not in nodes:
            raise RecordsAdapterError(
                f"record {focus} is not in store {cfg.id!r}", code="not_found", status=404
            )
        keep = _neighbourhood(edges, focus, max(0, depth))
        nodes = {k: v for k, v in nodes.items() if k in keep}
    total = len(nodes)
    truncated = total > limit
    if truncated:
        # Records first (newest first), then whatever they point at.
        ordered = sorted(
            nodes.values(),
            key=lambda n: (n["id"] != focus, n["kind"] != "record", _neg_time(n.get("recorded_at"))),
        )
        nodes = {n["id"]: n for n in ordered[:limit]}
    kept_edges = [e for e in edges.values() if e["source"] in nodes and e["target"] in nodes]
    return {
        "nodes": list(nodes.values()),
        "edges": kept_edges,
        "truncated": truncated,
        "total_nodes": total,
        "focus": focus,
    }


def _neg_time(value: Any) -> str:
    # Sort newest first inside an ascending sort: invert each character.
    text = str(value or "")
    return "".join(chr(0x10FFFF - ord(c)) for c in text)


__all__ = [
    "Availability",
    "RecordsAdapterError",
    "build_graph",
    "describe_store",
    "get_record",
    "list_records",
    "list_schemas",
    "record_graph",
    "records_available",
    "store_root",
]
