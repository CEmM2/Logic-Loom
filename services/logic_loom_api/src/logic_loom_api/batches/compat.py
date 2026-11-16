"""Legacy ``generation_plan.md`` <-> ``batches.json`` compatibility bridge.

Until the upstream AKMS internals are fully refactored, Logic-Loom keeps a
two-way bridge between the editable :class:`BatchStore` and the legacy
markdown the AKMS picker / parser already understand (see
``akms_nodes_gen.batch_picker.plan_parser.parse_plan``). The flow is::

    batches.json
      -> export_generation_plan(store) -> str (legacy markdown)
      -> AKMS parse_plan() (unchanged)
      -> existing picker UI/server behaviour

and on import::

    legacy generation_plan.md (text)
      -> import_generation_plan(text) -> list[EditableBatch]
      -> BatchStore.add(...) + save()

Round-trip rule: ``import_generation_plan(export_generation_plan(s))``
must reproduce ``s.batches`` for any batch set whose fields all fit inside
the legacy parser's vocabulary. The fixture
``services/logic_loom_api/tests/fixtures/generation_plan_v1.md`` is the
golden reference.

Lossy fields (legacy parser doesn't represent them — present in
:class:`EditableBatch` only):

* ``description`` (free-text — markdown sections after each batch table
  could be appended, but the parser ignores them)
* ``status``, ``source`` (workspace-only)
* ``created_at`` / ``updated_at`` (timestamps reset on import)
* ``papers`` (Zotero ids — the legacy plan stores PDF folder slugs, not
  paper ids; mapping is workspace-specific)
* ``notebook_id`` (NotebookLM id is workspace-side state)
* ``notes``, ``default_workflow_id``, ``prompt_profile_id``,
  ``prompt_overrides``, ``generation``

The round-trip fixture deliberately leaves these at defaults so the
identity test (`test_round_trip_export_import_is_noop`) holds. The export
function records the lossless subset; the import function reconstructs
:class:`EditableBatch` with defaults for the workspace-only fields.

Edge cases the legacy parser doesn't cover are flagged with a TODO +
clear error message rather than silently guessed (see
:func:`import_generation_plan` body).
"""

from __future__ import annotations

import re
from io import StringIO
from typing import Iterable

from .models import BatchNode, EditableBatch
from .store import BatchStore

# Header regexes mirror the legacy parser
# (akms_nodes_gen.batch_picker.plan_parser) so an export -> parse round
# trip works against the unchanged upstream parser too.
_BATCH_HEADER = re.compile(
    r"^##\s+(?P<id>R\d+_B\d+)\s*[—\-]\s*(?P<title>.+?)\s*\((?P<n>\d+)\s*nodes?\)\s*$"
)
_ROUND_HEADER = re.compile(r"^#\s+Round\s+(?P<r>\d+):\s*(?P<title>.+?)\s*$")
_FIELD = re.compile(r"^\*\*(?P<name>[^*]+?):\*\*\s*(?P<value>.+?)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|")
_NODE_ID = re.compile(r"`([^`]+)`")

# Legacy ID slug we round-trip on (R<n>_B<m>). EditableBatch.id may also
# carry workspace slugs; we only export batches whose id matches this
# pattern (others are workspace-only and silently skipped during export to
# avoid generating a markdown header the legacy parser can't read back).
_LEGACY_ID = re.compile(r"^R\d+_B\d+$")


# ---------------------------------------------------------------------------
# Export: batches -> markdown
# ---------------------------------------------------------------------------


def _node_size_from_tags(tags: Iterable[str]) -> str:
    """Pull a size hint out of the BatchNode tags.

    Legacy nodes carry ``size`` directly; EditableBatch's :class:`BatchNode`
    has a free-form ``tags`` list. The export prefers a tag literally
    matching ``"small"``, ``"medium"``, or ``"large"``; otherwise returns
    ``""`` (the legacy parser tolerates an empty size cell).
    """
    for t in tags:
        tl = t.lower()
        if tl in ("small", "medium", "large"):
            return tl
    return ""


def _format_batch_section(batch: EditableBatch) -> str:
    """Render one batch as the legacy markdown subsection.

    Output shape::

        ## R3_B2 — FFT Green Operators (2 nodes)
        **Sources:** ...

        | # | Node ID | Title | Size |
        |---|---------|-------|------|
        | 1 | `green-operator` | Green Operator | medium |
    """
    buf = StringIO()
    n = len(batch.nodes)
    buf.write(f"## {batch.id} — {batch.title} ({n} nodes)\n")
    if batch.sources_text:
        buf.write(f"**Sources:** {batch.sources_text}\n")
    if batch.zotsums_text:
        buf.write(f"**ZotSums:** {batch.zotsums_text}\n")
    buf.write("\n")
    if batch.nodes:
        buf.write("| # | Node ID | Title | Size |\n")
        buf.write("|---|---------|-------|------|\n")
        for i, node in enumerate(batch.nodes, 1):
            size = _node_size_from_tags(node.tags)
            buf.write(f"| {i} | `{node.node_id}` | {node.title} | {size} |\n")
    buf.write("\n")
    return buf.getvalue()


def export_generation_plan(store: BatchStore) -> str:
    """Render the store as a legacy ``generation_plan.md`` document.

    Batches are grouped by ``round`` (in numeric order; batches with
    ``round=None`` are skipped — the legacy format has no representation
    for round-less batches and we'd rather drop them than emit an
    unparseable header). Within a round, batches retain their store
    order. The output is deterministic: same input -> same output, no
    timestamps in the body, so the round-trip identity test is reliable.

    Batches whose ``id`` does not match ``R<n>_B<m>`` are skipped: the
    legacy parser's ``_BATCH_HEADER`` regex is the contract and arbitrary
    workspace slugs would not round-trip. Skipped batches log nothing —
    callers that need full export coverage should reshape ids upstream.
    """
    buf = StringIO()
    buf.write("# AKMS Node Generation Plan — Compatibility Export\n\n")
    buf.write(
        "> Generated by Logic-Loom batches/compat.py for legacy AKMS parse_plan()."
        "\n\n"
    )

    # Group by round, preserving in-store order within each round.
    rounds: dict[int, list[EditableBatch]] = {}
    for b in store.batches:
        if b.round is None or not _LEGACY_ID.match(b.id):
            continue
        rounds.setdefault(b.round, []).append(b)

    for r in sorted(rounds):
        batches = rounds[r]
        round_title = batches[0].round_title or f"Round {r}"
        round_theme = batches[0].round_theme
        buf.write(f"# Round {r}: {round_title}\n\n")
        if round_theme:
            buf.write(f"**Theme:** {round_theme}\n\n")
        for b in batches:
            buf.write(_format_batch_section(b))
        buf.write("---\n\n")

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Import: markdown -> batches
# ---------------------------------------------------------------------------


class GenerationPlanParseError(ValueError):
    """Raised on a malformed line the parser refuses to guess on.

    The task spec is explicit: "Edge cases in the legacy parser that the
    fixture doesn't cover get TODO + clear error message, not silent
    guesses." This is the clear-error half of that contract.
    """


def _parse_node_table(lines: list[str], start: int) -> tuple[list[BatchNode], int]:
    """Parse a single GFM table starting at or after ``start``.

    Returns ``(nodes, index_after_table)``. We deliberately mirror the
    upstream parser's column-detection logic so the import path tolerates
    the same format drift the upstream parser tolerates.
    """
    i = start
    while i < len(lines) and not _TABLE_ROW.match(lines[i]):
        i += 1
    if i >= len(lines):
        return [], i

    header_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
    i += 1
    if i < len(lines) and re.match(r"^\s*\|[-:\s|]+\|\s*$", lines[i]):
        i += 1

    low = [c.lower() for c in header_cells]

    def col(name_options: list[str]) -> int | None:
        for n in name_options:
            if n in low:
                return low.index(n)
        return None

    id_col = col(["node id", "id"])
    title_col = col(["title"])
    size_col = col(["size"])

    if id_col is None:
        raise GenerationPlanParseError(
            "node table is missing a 'Node ID' / 'ID' column header; "
            "TODO: extend the parser to handle a custom column layout"
        )

    nodes: list[BatchNode] = []
    while i < len(lines) and _TABLE_ROW.match(lines[i]):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if id_col < len(cells):
            m = _NODE_ID.search(cells[id_col])
            node_id = m.group(1) if m else cells[id_col]
            title = (
                cells[title_col].strip()
                if title_col is not None and title_col < len(cells)
                else ""
            )
            size = (
                cells[size_col].strip()
                if size_col is not None and size_col < len(cells)
                else ""
            )
            if node_id and not node_id.startswith("#"):
                tags = [size] if size else []
                nodes.append(BatchNode(node_id=node_id, title=title, tags=tags))
        i += 1
    return nodes, i


def import_generation_plan(text: str) -> list[EditableBatch]:
    """Parse a legacy ``generation_plan.md`` into :class:`EditableBatch`
    records.

    Returns one :class:`EditableBatch` per legacy batch header. Workspace-
    only fields (``status``, ``papers``, ``notebook_id``, ``notes``,
    ``prompt_profile_id``, etc.) are left at their model defaults — the
    caller (route layer) is responsible for merging if a batch with the
    same id already exists.

    Round context (``round``, ``round_title``, ``round_theme``) propagates
    from the most recently seen ``# Round N:`` header.

    Raises :class:`GenerationPlanParseError` if a node table is missing the
    required ``Node ID`` column (the one edge case the spec calls out as
    "clear error rather than silent guess"). All other malformed lines are
    tolerated to match the upstream parser's behaviour.

    ``source`` is set to ``"imported"`` on every produced batch so the UI
    can surface the provenance.
    """
    lines = text.splitlines()
    batches: list[EditableBatch] = []

    cur_round: int | None = None
    cur_round_title = ""
    cur_round_theme = ""

    i = 0
    while i < len(lines):
        line = lines[i]

        m_round = _ROUND_HEADER.match(line)
        if m_round:
            cur_round = int(m_round.group("r"))
            cur_round_title = m_round.group("title").strip()
            cur_round_theme = ""
            i += 1
            while i < len(lines):
                ln = lines[i]
                if _BATCH_HEADER.match(ln) or _ROUND_HEADER.match(ln):
                    break
                fm = _FIELD.match(ln)
                if fm:
                    name = fm.group("name").strip().lower()
                    val = fm.group("value").strip()
                    if name == "theme":
                        cur_round_theme = val
                i += 1
            continue

        m_batch = _BATCH_HEADER.match(line)
        if m_batch:
            batch_id = m_batch.group("id")
            title = m_batch.group("title").strip()
            sources_text = ""
            zotsums_text = ""
            nodes: list[BatchNode] = []
            i += 1
            while i < len(lines):
                ln = lines[i]
                if (
                    _BATCH_HEADER.match(ln)
                    or _ROUND_HEADER.match(ln)
                    or ln.strip() == "---"
                ):
                    break
                fm = _FIELD.match(ln)
                if fm:
                    name = fm.group("name").strip().lower()
                    val = fm.group("value").strip()
                    if name == "sources":
                        sources_text = val
                    elif name == "zotsums":
                        zotsums_text = val
                if _TABLE_ROW.match(ln) and not nodes:
                    nodes, j = _parse_node_table(lines, i)
                    i = j
                    continue
                i += 1
            batches.append(
                EditableBatch(
                    id=batch_id,
                    title=title,
                    round=cur_round,
                    round_title=cur_round_title,
                    round_theme=cur_round_theme,
                    source="imported",
                    sources_text=sources_text,
                    zotsums_text=zotsums_text,
                    nodes=nodes,
                )
            )
            continue

        i += 1

    return batches


# ---------------------------------------------------------------------------
# Round-trip helpers (consumed by the round-trip tests + the import endpoint)
# ---------------------------------------------------------------------------


def round_trip_via_markdown(store: BatchStore) -> list[EditableBatch]:
    """Convenience: ``export -> import`` against a store; returns the
    re-imported batch list. Used by the round-trip test.
    """
    text = export_generation_plan(store)
    return import_generation_plan(text)


def normalize_for_roundtrip(batch: EditableBatch) -> EditableBatch:
    """Strip the workspace-only fields the bridge cannot represent.

    Round-trip comparisons should ignore ``created_at`` / ``updated_at``
    (regenerated on import), ``source`` (always ``"imported"`` after a
    round trip), and other workspace-only state. This helper returns a
    fresh :class:`EditableBatch` with those fields cleared so equality
    against the re-imported set is meaningful.
    """
    return batch.model_copy(
        update={
            "created_at": "",
            "updated_at": "",
            "source": "imported",
            # Defaults that the legacy markdown can't carry.
            "papers": [],
            "notebook_id": None,
            "description": "",
            "status": "active",
            "notes": "",
            "default_workflow_id": None,
            "prompt_profile_id": None,
            "prompt_overrides": {},
            "generation": batch.generation.__class__(),
        }
    )
