"""``/api/prompts/*`` routes — the Prompt Library API.

CRUD over variants and profiles plus the resolver-facing
``activate`` and ``validate`` endpoints.

Conventions matching the §11 endpoint list:

* Variants are addressed by a combined ``(slot, variant_id)`` pair. The
  URL form takes the slot as a query parameter and the variant_id in the
  path so a single ``/variant/{id}`` URL doesn't need to embed dots.
* ``POST /validate`` runs the same path-safety check used by the Claude
  assets routes (subset of :func:`safe_asset_path`'s parts checks,
  generalised to prompt-library filenames which may be under
  ``prompts/`` or ``.claude/``) plus a "renders as expected MIME type"
  check based on ``content_type``.
* Mount preserves the Claude-assets / picker degrade-gracefully pattern in
  ``main.py``: a failure to mount logs a warning and the rest of the API
  stays up.

Error envelope: every user-facing failure surfaces as
``HTTPException(status_code=400, detail=...)`` so the global error
handler in :mod:`errors` wraps it in the uniform JSON envelope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import Settings
from ..prompts import (
    ActivePromptProfile,
    PromptProfile,
    PromptSlot,
    PromptStore,
    PromptVariant,
    make_batch_view,
    make_store_loaders,
    resolve_prompts_for_run,
)
from ..prompts.models import PromptSchema
from ..prompts.store import slugify

router = APIRouter()


def _store() -> PromptStore:
    """Build a :class:`PromptStore` rooted at the active workspace.

    A fresh :class:`Settings.load()` each call mirrors how the Claude
    assets routes pick up runtime config — the cost is negligible (TOML
    parse) and keeps test isolation simple (each TestClient resets state
    via env vars).
    """
    s = Settings.load()
    return PromptStore(s.akms_workspace.expanduser() / "prompt-library")


# Allowed runtime-target prefixes for the validate endpoint. These mirror
# the writeable surfaces a per-run materialised runtime exposes
# (`safe_asset_path` accepts `.claude/` and `scripts/`; prompts additionally
# target `prompts/` for zsum-family variants).
_ALLOWED_FILENAME_PREFIXES: tuple[str, ...] = (".claude/", "scripts/", "prompts/")


def _is_safe_filename(filename: str) -> bool:
    """Return ``True`` if ``filename`` is safe for runtime overlay use.

    Rules:
      * Non-empty.
      * Not absolute.
      * No ``..`` traversal parts.
      * Starts with one of :data:`_ALLOWED_FILENAME_PREFIXES`.
    """
    if not filename:
        return False
    p = Path(filename)
    if p.is_absolute():
        return False
    if ".." in p.parts:
        return False
    return any(filename.startswith(prefix) for prefix in _ALLOWED_FILENAME_PREFIXES)


def _expected_content_type_ok(content_type: str, content: str) -> bool:
    """Light "renders as expected MIME" sanity check.

    Rejects empty/whitespace-only content for both content types — a blank
    variant is never useful at runtime. For ``text`` also rejects content
    starting with a markdown front-matter block (``---\\n...---\\n``), a
    common copy-paste mistake. Beyond those guards the check stays
    permissive — the goal is to catch user-facing typos, not enforce a
    strict parser.
    """
    if not content or not content.strip():
        return False
    if content_type == "markdown":
        return True
    # text — reject obvious markdown
    return not content.lstrip().startswith("---\n")


# --------------------------------------------------------------------------
# GET /slots — enumerate the eight canonical slots
# --------------------------------------------------------------------------


@router.get("/slots")
def list_slots() -> dict[str, Any]:
    """Return the eight canonical prompt slot ids."""
    return {"slots": list(PromptSlot.all_ids())}


# --------------------------------------------------------------------------
# Variants
# --------------------------------------------------------------------------


@router.get("/variants")
def list_variants(slot: str | None = Query(default=None)) -> dict[str, Any]:
    """List variants on disk, optionally filtered by slot id.

    Bundled defaults are appended at the end so the UI can group them after
    user-authored variants without an extra round trip.
    """
    if slot is not None and slot not in PromptSlot.all_ids():
        raise HTTPException(status_code=400, detail=f"unknown slot: {slot!r}")
    store = _store()
    user_variants = [v.model_dump() for v in store.list_variants(slot_id=slot)]
    bundled = [
        v.model_dump()
        for v in store.list_bundled_variants()
        if slot is None or v.slot == slot
    ]
    return {"variants": user_variants, "bundled": bundled}


@router.get("/variant/{variant_id}")
def get_variant(variant_id: str, slot: str = Query(...)) -> dict[str, Any]:
    """Return a single user variant by (slot, variant_id)."""
    if slot not in PromptSlot.all_ids():
        raise HTTPException(status_code=400, detail=f"unknown slot: {slot!r}")
    store = _store()
    try:
        v = store.load_variant(slot, variant_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return v.model_dump()


class _VariantPayload(BaseModel):
    """Body for POST/PUT ``/variant``."""

    id: str | None = None
    family: str | None = None
    slot: str
    name: str
    description: str = ""
    based_on: str | None = None
    filename: str
    content_type: str = "markdown"
    content: str = ""
    output_schema_id: str | None = None


def _payload_to_variant(payload: _VariantPayload, *, source: str = "user") -> PromptVariant:
    family = payload.family or payload.slot.split(".", 1)[0]
    variant_id = payload.id or slugify(payload.name)
    if not variant_id:
        raise HTTPException(
            status_code=400, detail="variant id required (or non-empty name to derive one)"
        )
    try:
        return PromptVariant(
            id=variant_id,
            family=family,  # type: ignore[arg-type]
            slot=payload.slot,
            name=payload.name,
            description=payload.description,
            source=source,  # type: ignore[arg-type]
            based_on=payload.based_on,
            filename=payload.filename,
            content_type=payload.content_type,  # type: ignore[arg-type]
            content=payload.content,
            output_schema_id=payload.output_schema_id,
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/variant")
def create_variant(payload: _VariantPayload) -> dict[str, Any]:
    """Create a new user variant. Returns the persisted record."""
    variant = _payload_to_variant(payload)
    store = _store()
    store.save_variant(variant)
    return variant.model_dump()


@router.put("/variant/{variant_id}")
def update_variant(variant_id: str, payload: _VariantPayload) -> dict[str, Any]:
    """Overwrite an existing user variant."""
    # Allow the body to omit id; default to URL path.
    if not payload.id:
        payload.id = variant_id
    if payload.id != variant_id:
        raise HTTPException(
            status_code=400,
            detail=f"path variant_id {variant_id!r} != body id {payload.id!r}",
        )
    variant = _payload_to_variant(payload)
    store = _store()
    store.save_variant(variant)
    return variant.model_dump()


@router.delete("/variant/{variant_id}")
def delete_variant(variant_id: str, slot: str = Query(...)) -> dict[str, Any]:
    """Delete a user variant. 404 if missing."""
    if slot not in PromptSlot.all_ids():
        raise HTTPException(status_code=400, detail=f"unknown slot: {slot!r}")
    store = _store()
    removed = store.delete_variant(slot, variant_id)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"variant not found: slot={slot!r} id={variant_id!r}",
        )
    return {"deleted": True, "slot": slot, "id": variant_id}


@router.post("/variant/{variant_id}/duplicate")
def duplicate_variant(
    variant_id: str,
    slot: str = Query(...),
    new_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Duplicate an existing variant under a new id.

    Default new id: ``<variant_id>-copy``. Bundled variants are read via
    ``read_user_or_bundled`` semantics — if the source is bundled, the
    duplicate is created as a user variant pointing back via ``based_on``.
    """
    if slot not in PromptSlot.all_ids():
        raise HTTPException(status_code=400, detail=f"unknown slot: {slot!r}")
    store = _store()
    # Try user variant first, fall back to bundled.
    try:
        source_variant = store.load_variant(slot, variant_id)
        based_on: str | None = source_variant.based_on
    except FileNotFoundError:
        bundled_match = [
            v for v in store.list_bundled_variants() if v.slot == slot and v.id == variant_id
        ]
        if not bundled_match:
            raise HTTPException(
                status_code=404,
                detail=f"variant not found to duplicate: slot={slot!r} id={variant_id!r}",
            )
        source_variant = bundled_match[0]
        based_on = f"bundled:{slot}.{variant_id}"

    dup_id = new_id or f"{variant_id}-copy"
    duplicate = source_variant.model_copy(
        update={
            "id": dup_id,
            "name": f"{source_variant.name} (copy)",
            "source": "user",
            "based_on": based_on,
            "content_hash": "",
        }
    )
    store.save_variant(duplicate)
    return duplicate.model_dump()


# --------------------------------------------------------------------------
# Slot activate (set the active variant ref in the global active profile)
# --------------------------------------------------------------------------


class _ActivatePayload(BaseModel):
    variant_ref: str


@router.post("/slot/{slot}/activate")
def activate_slot(slot: str, payload: _ActivatePayload) -> dict[str, Any]:
    """Set the variant_ref for a single slot in the global active profile.

    Convenience for the Prompt Library UI's "make active" button. Equivalent
    to PUT-ing the same ``active`` map on the active-profile endpoint, but
    expressed at slot granularity.
    """
    if slot not in PromptSlot.all_ids():
        raise HTTPException(status_code=400, detail=f"unknown slot: {slot!r}")
    store = _store()
    ap = store.load_active_profile()
    new_active = dict(ap.active)
    new_active[slot] = payload.variant_ref
    new_ap = ap.model_copy(update={"active": new_active})
    store.save_active_profile(new_ap)
    return new_ap.model_dump()


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------


@router.get("/profiles")
def list_profiles() -> dict[str, Any]:
    """Return all stored profiles + the current active profile."""
    store = _store()
    return {
        "profiles": [p.model_dump() for p in store.list_profiles()],
        "active": store.load_active_profile().model_dump(),
    }


class _ProfilePayload(BaseModel):
    id: str | None = None
    name: str
    description: str = ""
    active: dict[str, str] = {}


@router.post("/profiles")
def create_profile(payload: _ProfilePayload) -> dict[str, Any]:
    """Create a new profile."""
    pid = payload.id or slugify(payload.name)
    if not pid:
        raise HTTPException(
            status_code=400, detail="profile id required (or non-empty name to derive one)"
        )
    try:
        profile = PromptProfile(
            id=pid,
            name=payload.name,
            description=payload.description,
            active=dict(payload.active),
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    store = _store()
    store.save_profile(profile)
    return profile.model_dump()


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: str, payload: _ProfilePayload) -> dict[str, Any]:
    """Overwrite an existing profile."""
    if payload.id and payload.id != profile_id:
        raise HTTPException(
            status_code=400,
            detail=f"path profile_id {profile_id!r} != body id {payload.id!r}",
        )
    try:
        profile = PromptProfile(
            id=profile_id,
            name=payload.name,
            description=payload.description,
            active=dict(payload.active),
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    store = _store()
    store.save_profile(profile)
    return profile.model_dump()


# --------------------------------------------------------------------------
# Validate — path safety + content_type rendering check
# --------------------------------------------------------------------------


class _ValidatePayload(BaseModel):
    filename: str
    content_type: str = "markdown"
    content: str = ""


@router.post("/validate")
def validate_variant(payload: _ValidatePayload) -> dict[str, Any]:
    """Validate a candidate variant's filename + content_type before save.

    Two-tier validation:

    * **Hard errors (HTTP 400)** — currently only the path-safety check.
      The filename must be a relative path under ``{.claude/, scripts/,
      prompts/}`` with no ``..`` segments. These represent security
      failures that the route layer refuses to ack as "ok".
    * **Soft warnings (HTTP 200)** — empty body, or content that looks
      like markdown front-matter in a ``text`` variant. These are
      authoring-time hints, not save blockers, so we return them in a
      ``warnings`` array alongside ``ok=True`` and let the UI render
      them as banners. Returning 400 here (the previous behaviour) made
      the Validate button error every time a user duplicated a variant
      and cleared its body before clicking it.

    Response shape: ``{ok, filename, content_type, warnings: list[str]}``.
    """
    if not _is_safe_filename(payload.filename):
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsafe filename: {payload.filename!r}. Must be a relative "
                f"path starting with one of {_ALLOWED_FILENAME_PREFIXES} "
                "with no '..' segments."
            ),
        )
    warnings: list[str] = []
    if not payload.content or not payload.content.strip():
        warnings.append("content is empty — variant has no body yet.")
    elif not _expected_content_type_ok(payload.content_type, payload.content):
        warnings.append(
            f"content does not render as content_type={payload.content_type!r}: "
            "looks like markdown front-matter in a 'text' variant."
        )
    return {
        "ok": True,
        "filename": payload.filename,
        "content_type": payload.content_type,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# Import / Export
# --------------------------------------------------------------------------


class _ImportPayload(BaseModel):
    """Bundle of variants + profiles to import."""

    variants: list[_VariantPayload] = []
    profiles: list[_ProfilePayload] = []


class _SchemaPayload(BaseModel):
    """Body for POST/PUT ``/schemas``."""

    id: str | None = None
    name: str
    description: str = ""
    family: Literal["zsum", "akms", "assistant"] | None = None
    content: dict = Field(default_factory=dict)


def _family_filter(
    schemas: list[Any], family: str | None
) -> list[Any]:
    """Keep schemas whose ``family`` matches ``family`` or is unscoped.

    Unscoped (``family is None``) schemas appear under every
    family so user-created schemas without a family tag stay visible
    until the user pins them.
    """
    if not family:
        return schemas
    return [s for s in schemas if s.family is None or s.family == family]


@router.get("/schemas")
def list_schemas(
    family: Literal["zsum", "akms", "assistant"] | None = Query(
        None, description="Restrict to schemas tagged with this family (plus unscoped)."
    ),
) -> dict[str, Any]:
    """List user-saved schemas plus the bundled defaults.

    When ``family`` is provided, the response is filtered: only schemas
    whose ``family`` matches (or whose ``family is None``) are returned.
    Unscoped schemas appear under every family by design.
    """
    store = _store()
    user = _family_filter(store.list_schemas(), family)
    bundled = _family_filter(store.list_bundled_schemas(), family)
    return {
        "schemas": [s.model_dump() for s in user],
        "bundled": [s.model_dump() for s in bundled],
    }


@router.get("/schemas/{schema_id}")
def get_schema(schema_id: str) -> dict[str, Any]:
    """Return a single schema. Falls back to bundled defaults if absent."""
    store = _store()
    try:
        return store.load_schema(schema_id).model_dump()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/schemas")
def create_schema(payload: _SchemaPayload) -> dict[str, Any]:
    """Create a new user schema. ``id`` is derived from ``name`` if omitted."""
    sid = payload.id or slugify(payload.name)
    if not sid:
        raise HTTPException(
            status_code=400, detail="schema id required (or non-empty name to derive one)"
        )
    try:
        schema = PromptSchema(
            id=sid,
            name=payload.name,
            description=payload.description,
            source="user",
            family=payload.family,
            content=payload.content,
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _store().save_schema(schema)
    return schema.model_dump()


@router.put("/schemas/{schema_id}")
def update_schema(schema_id: str, payload: _SchemaPayload) -> dict[str, Any]:
    """Overwrite an existing user schema."""
    if payload.id and payload.id != schema_id:
        raise HTTPException(
            status_code=400,
            detail=f"path schema_id {schema_id!r} != body id {payload.id!r}",
        )
    try:
        schema = PromptSchema(
            id=schema_id,
            name=payload.name,
            description=payload.description,
            source="user",
            family=payload.family,
            content=payload.content,
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _store().save_schema(schema)
    return schema.model_dump()


@router.delete("/schemas/{schema_id}")
def delete_schema(schema_id: str) -> dict[str, Any]:
    """Delete a user schema. 404 if missing (bundled defaults are read-only)."""
    removed = _store().delete_schema(schema_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"schema not found: {schema_id!r}")
    return {"deleted": True, "id": schema_id}


@router.post("/import")
def import_bundle(payload: _ImportPayload) -> dict[str, Any]:
    """Bulk-import variants and profiles. Idempotent (overwrites by id)."""
    store = _store()
    imported_variants = 0
    imported_profiles = 0
    for vp in payload.variants:
        variant = _payload_to_variant(vp)
        store.save_variant(variant)
        imported_variants += 1
    for pp in payload.profiles:
        pid = pp.id or slugify(pp.name)
        if not pid:
            raise HTTPException(status_code=400, detail="profile id required")
        store.save_profile(
            PromptProfile(
                id=pid,
                name=pp.name,
                description=pp.description,
                active=dict(pp.active),
            )
        )
        imported_profiles += 1
    return {
        "imported_variants": imported_variants,
        "imported_profiles": imported_profiles,
    }


@router.get("/export/{export_id}")
def export_bundle(export_id: str) -> dict[str, Any]:
    """Export a single profile + every variant referenced by it.

    ``export_id`` must be a profile id. The response carries the profile
    plus the materialised variants so the user can copy the bundle into
    another workspace.
    """
    store = _store()
    try:
        profile = store.load_profile(export_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    variants: list[dict[str, Any]] = []
    # Build a (slot, variant_id) set from the profile's active refs.
    from ..prompts.resolver import parse_variant_ref

    for ref in profile.active.values():
        try:
            source, slot, vid = parse_variant_ref(ref)
        except ValueError:
            continue
        if source == "user":
            try:
                variants.append(store.load_variant(slot, vid).model_dump())
            except FileNotFoundError:
                continue
    return {"profile": profile.model_dump(), "variants": variants}


# --------------------------------------------------------------------------
# Resolve — run-time callers use the resolver module directly; this
# convenience wrapper is exposed here for the UI's "effective prompts" panel.
# --------------------------------------------------------------------------


class _ResolveRequestPayload(BaseModel):
    batch_prompt_profile_id: str | None = None
    batch_prompt_overrides: dict[str, str] = {}
    request_profile_id: str | None = None
    request_overrides: dict[str, str] = {}


@router.post("/resolve")
def resolve_effective_prompts(payload: _ResolveRequestPayload) -> dict[str, Any]:
    """Run the six-tier resolver and return the effective slot→variant map.

    A thin wrapper for callers such as the Run dialog's effective-prompts
    preview. Bundled defaults are consulted via the store's :meth:`list_bundled_variants` so the wrapper
    survives missing user resources gracefully.
    """
    store = _store()
    profile_loader, variant_loader = make_store_loaders(store)
    batch = make_batch_view(
        prompt_profile_id=payload.batch_prompt_profile_id,
        prompt_overrides=payload.batch_prompt_overrides,
    )
    try:
        eff = resolve_prompts_for_run(
            batch=batch,
            global_profile=store.load_active_profile(),
            request_profile_id=payload.request_profile_id,
            request_overrides=payload.request_overrides,
            profile_loader=profile_loader,
            variant_loader=variant_loader,
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return eff.model_dump()


# --------------------------------------------------------------------------
# Migration import endpoints with preview/merge/replace
# triad. Each enforces the preview-token-required-for-replace invariant
# (see :mod:`logic_loom_api.import_tokens`).
# --------------------------------------------------------------------------


class _ImportClaudeAssetsBody(BaseModel):
    """POST /api/prompts/import-akms-claude-assets body.

    ``source_dir`` is a path to a directory holding ``.claude/`` and/or
    ``scripts/`` overlay files (e.g. an old ``akms_repo`` checkout). When
    omitted we default to ``<Settings.akms_repo>/Packages/AKMS/.claude``.
    """

    model_config = ConfigDict(extra="ignore")
    mode: str = "preview"
    source_dir: str | None = None
    preview_token: str | None = Field(default=None, description="Required for mode=replace.")


_IMPORT_CLAUDE_ASSETS_ENDPOINT = "import-akms-claude-assets"
_IMPORT_ZSUM_PROMPTS_ENDPOINT = "import-zsum-prompts"


def _claude_default_source() -> Path:
    """Default import source: ``<akms_repo>/Packages/AKMS/.claude``.

    Only available with the optional ``akms_repo`` checkout; otherwise the
    caller must pass ``source_dir`` (400, the same status a missing source
    already got).
    """
    repo = Settings.load().akms_repo_dir()
    if repo is None:
        raise HTTPException(
            status_code=400,
            detail="source_dir is required: no akms_repo source checkout is configured.",
        )
    return repo / "Packages" / "AKMS" / ".claude"


def _scan_overlay(source: Path, allowed_prefixes: tuple[str, ...]) -> list[dict[str, Any]]:
    """List files under ``source`` whose relative-to-source path starts
    with one of ``allowed_prefixes``. Each entry: ``{rel_path, size}``.
    """
    entries: list[dict[str, Any]] = []
    for p in sorted(source.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(source).as_posix()
        if not any(rel.startswith(prefix) for prefix in allowed_prefixes):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        entries.append({"rel_path": rel, "size": size})
    return entries


def _claude_runtime_dest() -> Path:
    s = Settings.load()
    dest = s.akms_workspace.expanduser() / "claude-runtime"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _zsum_runtime_dest() -> Path:
    s = Settings.load()
    dest = s.akms_workspace.expanduser() / "zsum-runtime" / "prompts"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _classify(source_files: list[dict[str, Any]], dest: Path) -> dict[str, list[str]]:
    new: list[str] = []
    identical: list[str] = []
    conflict: list[str] = []
    for entry in source_files:
        rel = entry["rel_path"]
        dpath = dest / rel
        if not dpath.exists():
            new.append(rel)
        elif dpath.stat().st_size == entry["size"]:
            identical.append(rel)
        else:
            conflict.append(rel)
    return {"new": new, "identical": identical, "conflict": conflict}


def _do_copy(source_root: Path, dest: Path, rels: list[str]) -> None:
    for rel in rels:
        src_file = source_root / rel
        dest_file = dest / rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_bytes(src_file.read_bytes())


def _run_import(
    *,
    endpoint_id: str,
    mode: str,
    preview_token: str | None,
    source: Path,
    allowed_prefixes: tuple[str, ...],
    dest: Path,
) -> dict[str, Any]:
    """Shared preview/merge/replace flow for the two prompts-router import
    endpoints. Returns the response envelope.
    """
    from ..import_tokens import ImportTokenError, registry as _token_registry

    if mode not in ("preview", "merge", "replace"):
        raise HTTPException(status_code=400, detail=f"unknown import mode: {mode!r}")
    if not source.exists() or not source.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"source_dir does not exist or is not a directory: {source}",
        )
    source_files = _scan_overlay(source, allowed_prefixes)
    diff = _classify(source_files, dest)

    if mode == "preview":
        token = _token_registry().mint(endpoint_id, payload={"count": len(source_files)})
        return {
            "mode": "preview",
            "source_dir": str(source),
            "dest": str(dest),
            "diff": diff,
            "preview_token": token,
        }

    if mode == "replace":
        try:
            _token_registry().consume(endpoint_id, preview_token)
        except ImportTokenError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        to_copy = diff["new"] + diff["conflict"]
        _do_copy(source, dest, to_copy)
        return {
            "mode": "replace",
            "source_dir": str(source),
            "dest": str(dest),
            "written": to_copy,
            "preserved": diff["identical"],
        }

    # merge
    _do_copy(source, dest, diff["new"])
    return {
        "mode": "merge",
        "source_dir": str(source),
        "dest": str(dest),
        "written": diff["new"],
        "skipped_conflicts": diff["conflict"],
        "preserved": diff["identical"],
    }


@router.post("/import-akms-claude-assets")
def import_akms_claude_assets(body: _ImportClaudeAssetsBody) -> dict[str, Any]:
    """Migrate ``.claude/`` and ``scripts/`` overlays from an AKMS repo into
    ``<akms_workspace>/claude-runtime/``.

    Modes: preview / merge / replace. ``replace`` requires the
    ``preview_token`` returned by the matching ``preview`` call.
    """
    src = Path(body.source_dir).expanduser() if body.source_dir else _claude_default_source()
    return _run_import(
        endpoint_id=_IMPORT_CLAUDE_ASSETS_ENDPOINT,
        mode=body.mode,
        preview_token=body.preview_token,
        source=src,
        allowed_prefixes=(".claude/", "scripts/"),
        dest=_claude_runtime_dest(),
    )


class _ImportZsumPromptsBody(BaseModel):
    """POST /api/prompts/import-zsum-prompts body.

    ``source_dir`` defaults to ``<Settings.zsum_repo>/prompts`` (the
    legacy ``ZSUM_PROMPT_DIR`` layout).
    """

    model_config = ConfigDict(extra="ignore")
    mode: str = "preview"
    source_dir: str | None = None
    preview_token: str | None = Field(default=None, description="Required for mode=replace.")


def _zsum_default_source() -> Path:
    """Default import source: ``<zsum_repo>/prompts`` (optional checkout).

    Without a ``zsum_repo`` checkout the caller must pass ``source_dir``.
    """
    repo = Settings.load().zsum_repo_dir()
    if repo is None:
        raise HTTPException(
            status_code=400,
            detail="source_dir is required: no zsum_repo source checkout is configured.",
        )
    return repo / "prompts"


@router.post("/import-zsum-prompts")
def import_zsum_prompts(body: _ImportZsumPromptsBody) -> dict[str, Any]:
    """Migrate a legacy ``ZSUM_PROMPT_DIR`` tree into
    ``<akms_workspace>/zsum-runtime/prompts/``.

    Modes: preview / merge / replace. ``replace`` requires the
    ``preview_token`` returned by the matching ``preview`` call.
    """
    src = Path(body.source_dir).expanduser() if body.source_dir else _zsum_default_source()
    # The zsum tree is flat (no overlay prefix), so accept every file.
    return _run_import(
        endpoint_id=_IMPORT_ZSUM_PROMPTS_ENDPOINT,
        mode=body.mode,
        preview_token=body.preview_token,
        source=src,
        allowed_prefixes=("",),
        dest=_zsum_runtime_dest(),
    )
