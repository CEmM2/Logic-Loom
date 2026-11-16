"""``/api/batches/*`` routes — the Batch CRUD API.

Endpoints: CRUD over batches, papers add/remove/move + bulk-add, nodes
CRUD, prompts get/put/effective-prompts/reset.

The endpoints persist through the :class:`BatchStore` rooted
at ``<akms_workspace>/batches.json``. Effective-prompts delegates to
:func:`resolve_prompts_for_run` with no adapter glue — the
:class:`EditableBatch` satisfies the :class:`PromptBatchLike` Protocol
directly.

Error envelope: every user-facing failure surfaces as
``HTTPException(status_code={400,404,409})`` so the global error handler
in :mod:`errors` wraps it in the uniform JSON envelope.

Mount: follows the Claude assets / prompts degrade-gracefully pattern in
``main.py`` — a failure to mount logs a warning and the rest of the API
stays up.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..batches import (
    BatchGenerationDefaults,
    BatchNode,
    BatchStore,
    EditableBatch,
)
from ..batches.compat import (
    GenerationPlanParseError,
    export_generation_plan,
    import_generation_plan,
)
from ..config import Settings
from ..prompts import (
    ActivePromptProfile,
    PromptStore,
    make_store_loaders,
    resolve_prompts_for_run,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Store accessors
# ---------------------------------------------------------------------------


def _batch_root() -> "Path":  # noqa: F821 — Path imported inside fn for symmetry
    from pathlib import Path

    s = Settings.load()
    return Path(s.akms_workspace).expanduser()


def _load_store() -> BatchStore:
    """Build a fresh :class:`BatchStore` rooted at the active workspace.

    Reading per-request mirrors the prompts route style — keeps tests easy
    to isolate via ``LOGIC_LOOM_AKMS_WORKSPACE`` and stays correct under the
    "no in-process cache" model the rest of the sidecar already uses.
    """
    return BatchStore.load(_batch_root())


def _save_store(store: BatchStore) -> None:
    store.save(_batch_root())


def _prompt_store() -> PromptStore:
    from pathlib import Path

    s = Settings.load()
    return PromptStore(Path(s.akms_workspace).expanduser() / "prompt-library")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _get_or_404(store: BatchStore, batch_id: str) -> EditableBatch:
    b = store.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail=f"batch not found: {batch_id!r}")
    return b


# ---------------------------------------------------------------------------
# Request bodies (loose Pydantic models — keep separate from EditableBatch
# so PATCH semantics stay obvious)
# ---------------------------------------------------------------------------


class CreateBatchBody(BaseModel):
    """POST /api/batches body. ``title`` is required; the id is minted by
    the store from ``slugify_title(title)`` with collision suffixes.
    """

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1)
    description: str = ""
    round: int | None = None
    round_title: str = ""
    round_theme: str = ""
    papers: list[str] = Field(default_factory=list)
    nodes: list[BatchNode] = Field(default_factory=list)
    notebook_id: str | None = None
    zotsums_text: str = ""
    sources_text: str = ""
    notes: str = ""
    default_workflow_id: str | None = None
    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    generation: BatchGenerationDefaults | None = None


class PatchBatchBody(BaseModel):
    """PATCH /api/batches/{id} body. Every field is optional; ``id`` is not
    patchable (always derived from ``title`` at create time).
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    description: str | None = None
    round: int | None = None
    round_title: str | None = None
    round_theme: str | None = None
    status: str | None = None
    notebook_id: str | None = None
    zotsums_text: str | None = None
    sources_text: str | None = None
    notes: str | None = None
    default_workflow_id: str | None = None
    generation: BatchGenerationDefaults | None = None


class PapersAddBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    papers: list[str] = Field(default_factory=list)


class PapersRemoveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    papers: list[str] = Field(default_factory=list)


class PapersMoveBody(BaseModel):
    """Move a single paper from one index to another."""

    model_config = ConfigDict(extra="ignore")
    paper: str
    to_index: int = Field(ge=0)


class BulkAddBody(BaseModel):
    """Bulk-add accepts either a list of paper ids or a list of node specs;
    we lean on whichever field is present. Both being absent is an error.
    """

    model_config = ConfigDict(extra="ignore")
    papers: list[str] = Field(default_factory=list)
    nodes: list[BatchNode] = Field(default_factory=list)


class PromptsPutBody(BaseModel):
    """PUT /api/batches/{id}/prompts body — replaces both prompt fields."""

    model_config = ConfigDict(extra="ignore")
    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)


class PatchNodeBody(BaseModel):
    """PATCH /api/batches/{id}/nodes/{node_id} body. Every field optional."""

    model_config = ConfigDict(extra="ignore")
    title: str | None = None
    description: str | None = None
    domain: str | None = None
    tags: list[str] | None = None
    priority: int | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# Batch-level CRUD
# ---------------------------------------------------------------------------


@router.get("")
def list_batches() -> dict[str, Any]:
    """Return every batch in the workspace, ordered by store position."""
    store = _load_store()
    return {"batches": [b.model_dump() for b in store.batches]}


# IMPORTANT: declare static routes (import-plan, export-plan) BEFORE the
# parametric ``/{batch_id}`` route so FastAPI's first-match routing does
# not interpret e.g. ``/api/batches/export-plan`` as ``batch_id="export-plan"``.


class _ImportPlanBody(BaseModel):
    """POST /api/batches/import-plan body.

    ``text`` is the raw legacy markdown. ``mode`` mirrors the §13 import
    modes table: ``preview`` returns the parsed batches without writing;
    ``merge`` adds new batches and skips ids already present; ``replace``
    overwrites by id. Defaults to ``preview`` so an accidental call is
    side-effect free.
    """

    model_config = ConfigDict(extra="ignore")
    text: str = Field(min_length=1)
    mode: str = "preview"
    preview_token: str | None = Field(default=None, description="Required for mode=replace.")


_IMPORT_PLAN_ENDPOINT_ID = "import-plan"


@router.post("/import-plan")
def import_plan(body: _ImportPlanBody) -> dict[str, Any]:
    """Parse a legacy ``generation_plan.md`` into the workspace.

    Modes:

    * ``preview`` (default) — parse only, no writes; returns a single-use
      ``preview_token`` required for a subsequent ``replace`` call.
    * ``merge`` — write any parsed batch whose id is not already in the
      store; existing ids are skipped (reported in the response). No
      token required.
    * ``replace`` — write every parsed batch, overwriting existing
      records by id. REQUIRES a valid ``preview_token`` from a recent
      ``preview`` of this endpoint. Returns
      400 if the token is missing / stale / for a different endpoint /
      already consumed.

    Errors:
        * 400 ``GenerationPlanParseError`` — malformed legacy markdown the
          parser refuses to silently guess on (e.g. a node table missing
          the ``Node ID`` column).
        * 400 ``unknown mode`` — typo on the ``mode`` field.
        * 400 ``preview_token …`` — see :mod:`logic_loom_api.import_tokens`.
    """
    from ..import_tokens import ImportTokenError, registry as _token_registry

    if body.mode not in ("preview", "merge", "replace"):
        raise HTTPException(
            status_code=400, detail=f"unknown import mode: {body.mode!r}"
        )

    try:
        parsed = import_generation_plan(body.text)
    except GenerationPlanParseError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if body.mode == "preview":
        token = _token_registry().mint(
            _IMPORT_PLAN_ENDPOINT_ID, payload={"count": len(parsed)}
        )
        return {
            "mode": "preview",
            "parsed": [b.model_dump() for b in parsed],
            "written": [],
            "skipped": [],
            "preview_token": token,
        }

    if body.mode == "replace":
        try:
            _token_registry().consume(_IMPORT_PLAN_ENDPOINT_ID, body.preview_token)
        except ImportTokenError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    store = _load_store()
    written: list[str] = []
    skipped: list[str] = []
    for b in parsed:
        existing = store.get(b.id)
        if existing is None:
            store.add(b)
            written.append(b.id)
        elif body.mode == "replace":
            store.replace(b)
            written.append(b.id)
        else:  # merge — keep existing
            skipped.append(b.id)
    _save_store(store)
    return {
        "mode": body.mode,
        "parsed": [b.model_dump() for b in parsed],
        "written": written,
        "skipped": skipped,
    }


@router.get("/export-plan")
def export_plan() -> dict[str, Any]:
    """Render the workspace as a legacy ``generation_plan.md`` text body.

    Returned as a JSON envelope ``{"text": "..."}`` so the standard
    correlation-id middleware + error envelope stay in effect.
    """
    store = _load_store()
    return {"text": export_generation_plan(store)}


@router.get("/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    """Return one batch by id."""
    store = _load_store()
    return _get_or_404(store, batch_id).model_dump()


@router.post("", status_code=201)
def create_batch(body: CreateBatchBody) -> dict[str, Any]:
    """Create a new batch; the id is minted from the title slug with
    collision suffix if needed.
    """
    store = _load_store()
    try:
        new_id = store.new_id_from_title(body.title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    payload = body.model_dump(exclude_none=True)
    payload.setdefault("generation", BatchGenerationDefaults().model_dump())
    payload["id"] = new_id
    try:
        batch = EditableBatch(**payload)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors()) from e
    store.add(batch)
    _save_store(store)
    return batch.model_dump()


@router.patch("/{batch_id}")
def patch_batch(batch_id: str, body: PatchBatchBody) -> dict[str, Any]:
    """Partial update. Only the supplied fields change; ``updated_at`` is
    refreshed automatically.
    """
    store = _load_store()
    existing = _get_or_404(store, batch_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return existing.model_dump()
    merged = existing.model_dump()
    merged.update(updates)
    merged["updated_at"] = _utc_now_iso()
    try:
        new_batch = EditableBatch(**merged)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors()) from e
    store.replace(new_batch)
    _save_store(store)
    return new_batch.model_dump()


@router.post("/{batch_id}/duplicate", status_code=201)
def duplicate_batch(batch_id: str) -> dict[str, Any]:
    """Create a deep copy with a new id (and ``source = "duplicated"``).

    The new id is derived from the original's title plus the standard
    ``-2``/``-3``/... collision suffix. Every list/dict is deep-copied so
    mutating the new batch never touches the original.
    """
    store = _load_store()
    src = _get_or_404(store, batch_id)
    new_id = store.new_id_from_title(src.title)
    now = _utc_now_iso()
    clone_data = src.model_dump()
    clone_data = copy.deepcopy(clone_data)
    clone_data["id"] = new_id
    clone_data["source"] = "duplicated"
    clone_data["created_at"] = now
    clone_data["updated_at"] = now
    new_batch = EditableBatch(**clone_data)
    store.add(new_batch)
    _save_store(store)
    return new_batch.model_dump()


@router.post("/{batch_id}/archive")
def archive_batch(batch_id: str) -> dict[str, Any]:
    """Mark a batch as ``status = "archived"`` without removing it.

    Distinct from DELETE — archive is the soft-delete UX the picker uses
    to hide a batch from the active list while preserving history.
    """
    store = _load_store()
    existing = _get_or_404(store, batch_id)
    updated = existing.model_copy(update={"status": "archived", "updated_at": _utc_now_iso()})
    store.replace(updated)
    _save_store(store)
    return updated.model_dump()


@router.delete("/{batch_id}", status_code=204)
def delete_batch(batch_id: str) -> None:
    """Hard-delete a batch. The archive endpoint is the soft-delete path."""
    store = _load_store()
    if store.get(batch_id) is None:
        raise HTTPException(status_code=404, detail=f"batch not found: {batch_id!r}")
    store.remove(batch_id)
    _save_store(store)


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------


def _mutate_save(store: BatchStore, batch: EditableBatch, **updates) -> EditableBatch:
    """Helper: apply updates via ``model_copy``, persist, return new batch."""
    updates.setdefault("updated_at", _utc_now_iso())
    new = batch.model_copy(update=updates)
    store.replace(new)
    _save_store(store)
    return new


@router.post("/{batch_id}/papers/add")
def papers_add(batch_id: str, body: PapersAddBody) -> dict[str, Any]:
    """Append the given paper ids to the batch's papers list. Existing
    duplicates are preserved (the picker treats ordering as user intent).
    """
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    new_papers = list(batch.papers) + list(body.papers)
    return _mutate_save(store, batch, papers=new_papers).model_dump()


@router.post("/{batch_id}/papers/remove")
def papers_remove(batch_id: str, body: PapersRemoveBody) -> dict[str, Any]:
    """Remove the given paper ids (all occurrences) from the batch."""
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    rm = set(body.papers)
    new_papers = [p for p in batch.papers if p not in rm]
    return _mutate_save(store, batch, papers=new_papers).model_dump()


@router.post("/{batch_id}/papers/move")
def papers_move(batch_id: str, body: PapersMoveBody) -> dict[str, Any]:
    """Move a paper to a new index. 400 if the paper isn't in the batch."""
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    if body.paper not in batch.papers:
        raise HTTPException(status_code=400, detail=f"paper not in batch: {body.paper!r}")
    new_papers = [p for p in batch.papers if p != body.paper]
    new_index = max(0, min(body.to_index, len(new_papers)))
    new_papers.insert(new_index, body.paper)
    return _mutate_save(store, batch, papers=new_papers).model_dump()


@router.post("/{batch_id}/bulk-add")
def bulk_add(batch_id: str, body: BulkAddBody) -> dict[str, Any]:
    """Append both papers and nodes in a single call (either may be empty).

    400 if both lists are empty — the endpoint exists to amortise round
    trips, so an empty call is treated as a client bug.
    """
    if not body.papers and not body.nodes:
        raise HTTPException(status_code=400, detail="bulk-add requires papers or nodes")
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    new_papers = list(batch.papers) + list(body.papers)
    new_nodes = list(batch.nodes) + list(body.nodes)
    return _mutate_save(store, batch, papers=new_papers, nodes=new_nodes).model_dump()


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@router.post("/{batch_id}/nodes", status_code=201)
def add_node(batch_id: str, node: BatchNode) -> dict[str, Any]:
    """Append a node to the batch. 409 if a node with the same id already
    exists in this batch.
    """
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    if any(n.node_id == node.node_id for n in batch.nodes):
        raise HTTPException(
            status_code=409,
            detail=f"node id already present in batch: {node.node_id!r}",
        )
    new_nodes = list(batch.nodes) + [node]
    return _mutate_save(store, batch, nodes=new_nodes).model_dump()


@router.patch("/{batch_id}/nodes/{node_id}")
def patch_node(batch_id: str, node_id: str, body: PatchNodeBody) -> dict[str, Any]:
    """Partial update on a node within a batch. 404 if the node is missing."""
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    new_nodes = list(batch.nodes)
    for i, n in enumerate(new_nodes):
        if n.node_id == node_id:
            updates = body.model_dump(exclude_none=True)
            merged = n.model_dump()
            merged.update(updates)
            try:
                new_nodes[i] = BatchNode(**merged)
            except ValidationError as e:
                raise HTTPException(status_code=400, detail=e.errors()) from e
            return _mutate_save(store, batch, nodes=new_nodes).model_dump()
    raise HTTPException(status_code=404, detail=f"node not found: {node_id!r}")


@router.delete("/{batch_id}/nodes/{node_id}", status_code=204)
def delete_node(batch_id: str, node_id: str) -> None:
    """Remove a node from the batch. 404 if the node is missing."""
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    new_nodes = [n for n in batch.nodes if n.node_id != node_id]
    if len(new_nodes) == len(batch.nodes):
        raise HTTPException(status_code=404, detail=f"node not found: {node_id!r}")
    _mutate_save(store, batch, nodes=new_nodes)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@router.get("/{batch_id}/prompts")
def get_prompts(batch_id: str) -> dict[str, Any]:
    """Return the batch's saved prompt config.

    Distinct from ``/effective-prompts``: this is the raw stored config
    (profile id + overrides), not the resolved variant set.
    """
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    return {
        "prompt_profile_id": batch.prompt_profile_id,
        "prompt_overrides": dict(batch.prompt_overrides),
    }


@router.put("/{batch_id}/prompts")
def put_prompts(batch_id: str, body: PromptsPutBody) -> dict[str, Any]:
    """Replace the batch's prompt config wholesale.

    Use this for the §6 "save as batch default" UX — the run dialog wires
    its checkbox here so the mutation is explicit, never a side-effect of
    a GET.
    """
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    updated = _mutate_save(
        store,
        batch,
        prompt_profile_id=body.prompt_profile_id,
        prompt_overrides=dict(body.prompt_overrides),
    )
    return {
        "prompt_profile_id": updated.prompt_profile_id,
        "prompt_overrides": dict(updated.prompt_overrides),
    }


@router.get("/{batch_id}/effective-prompts")
def get_effective_prompts(batch_id: str) -> dict[str, Any]:
    """Resolve the batch's effective prompt set via the prompt resolver.

    Layers (lowest precedence first): global active profile → batch's
    profile (if set) → batch's overrides. No per-run profile or per-run
    overrides at this endpoint — those are layered in by the
    run-workflow handler at run time.

    Returns the :class:`EffectivePromptSet` shape (``slot_to_variant`` +
    ``contents``) so the run dialog can preview what the run will see
    without actually starting one.
    """
    store = _load_store()
    batch = _get_or_404(store, batch_id)

    p_store = _prompt_store()
    global_profile = p_store.load_active_profile()
    profile_loader, variant_loader = make_store_loaders(p_store)

    try:
        eps = resolve_prompts_for_run(
            batch=batch,
            global_profile=global_profile,
            request_profile_id=None,
            request_overrides=None,
            profile_loader=profile_loader,
            variant_loader=variant_loader,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return eps.model_dump()


@router.post("/{batch_id}/prompts/reset")
def reset_prompts(batch_id: str) -> dict[str, Any]:
    """Clear the batch's prompt config (profile + overrides).

    The batch falls back to the global active profile next time anyone
    resolves prompts against it. Used by the §6 "reset to defaults" UX.
    """
    store = _load_store()
    batch = _get_or_404(store, batch_id)
    updated = _mutate_save(
        store, batch, prompt_profile_id=None, prompt_overrides={}
    )
    return {
        "prompt_profile_id": updated.prompt_profile_id,
        "prompt_overrides": dict(updated.prompt_overrides),
    }
