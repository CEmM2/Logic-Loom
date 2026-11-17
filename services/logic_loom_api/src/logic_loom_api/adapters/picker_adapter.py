"""Adapter that mounts the AKMS batch_picker FastAPI app under ``/api/picker``.

The upstream package already exposes a behavior-preserving ``create_app(paths)``
factory and a ``Paths`` dataclass with env-var overrides, so this adapter is
purely glue: build a ``Paths`` from LogicLoom's ``Settings`` and hand it back.

No AKMS source checkout is required. The picker code comes from the installed
``akms_nodes_gen`` package and every path it reads resolves from the
Logic-Loom workspace (``akms_workspace``) and the configured vault.
``akms_repo`` is an optional, development-only override: it is consulted only
when it is set *and* names an existing directory.

Data-source preference
======================

The picker reads its batch records from a markdown ``generation_plan.md``
file. With the editable-batch :class:`BatchStore` in place, the
adapter prefers the workspace's ``batches.json`` as the source of
truth and materialises a compatibility-shaped ``generation_plan.md``
into ``<akms_workspace>/.cache/generation_plan.md`` on each picker
build. The legacy ``<akms_repo>/Packages/AKMS_nodes_gen/generation_plan.md``
is the fallback when no ``batches.json`` exists yet (existing AKMS
workspaces during the tester-release transition), and only when an
``akms_repo`` checkout is actually present.

Order of precedence:

1. ``<akms_workspace>/batches.json`` → export via
   :func:`logic_loom_api.batches.compat.export_generation_plan` into
   ``<akms_workspace>/.cache/generation_plan.md`` and hand that to the
   picker. The picker parses its plan once, so the adapter re-exports and
   reloads it whenever ``batches.json`` changes (checked on each picker
   request). Batches the legacy format can't name (workspace slugs such as
   ``starter-placeholder``, or batches with no round) are exported under
   stable picker ids ``R900_B<n>`` recorded in
   ``<akms_workspace>/.cache/picker_batch_ids.json``; before this they were
   silently dropped and the picker showed no batches.
2. ``<akms_repo>/Packages/AKMS_nodes_gen/generation_plan.md`` (legacy
   dev-time source; skipped when ``akms_repo`` is unset or missing).
3. Neither exists → raise ``FileNotFoundError`` so the ``main.py`` mount
   try/except logs a warning and the picker is skipped (CLAUDE.md
   degrade-gracefully invariant).

The Zotero catalog comes from ``<vault_path>/zsumbib.json``. With no
``vault_path`` the AKMS ``AKMS_ZOTSUMS_ROOT`` / ``AKMS_BBT_JSON`` environment
overrides are honoured, then zsum's default ``~/ZotSums`` — never the vendored
akms-nodes-gen 0.1.0 default, a cloud-synced folder on the AKMS author's
machine.

The ``akms_nodes_gen`` package being unavailable raises
:class:`RuntimeError` at construction time so the failure is loud rather than
a 500 on the first picker request.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

from ..config import Settings

logger = logging.getLogger(__name__)

# Subpath under ``<akms_workspace>`` where the materialised legacy plan
# lives. Kept under ``.cache/`` so it's clearly disposable — the
# ``batches.json`` is the source of truth, this file is a derived artefact
# the picker reads.
_PLAN_CACHE_RELPATH = Path(".cache") / "generation_plan.md"

# Persistent workspace-id -> picker-id map for batches the legacy plan format
# can't name. Ids are only ever added, never reused, so a picker assignment
# keyed by a picker id keeps pointing at the same batch.
_PICKER_ID_MAP_RELPATH = Path(".cache") / "picker_batch_ids.json"

# The synthetic round those batches are exported under. High enough not to
# collide with real AKMS rounds; collisions are skipped anyway.
_WORKSPACE_ROUND = 900
_WORKSPACE_ROUND_TITLE = "Workspace batches"
_LEGACY_ID = re.compile(r"^R(?P<round>\d+)_B\d+$")

# Location of the legacy plan inside an AKMS source checkout.
_LEGACY_PLAN_RELPATH = Path("Packages") / "AKMS_nodes_gen" / "generation_plan.md"


def _akms_repo_dir(settings: Settings) -> Path | None:
    """Return the optional AKMS source checkout, or ``None`` when absent.

    ``akms_repo`` is a development-only override: an unset value or a path
    that is not an existing directory both mean "no checkout", and callers
    then resolve everything from the installed package and the workspace.
    """
    raw = getattr(settings, "akms_repo", None)
    if raw is None:
        return None
    repo = Path(raw).expanduser()
    return repo if repo.is_dir() else None


def build_picker_app(settings: Settings) -> FastAPI:
    """Return a fully-configured AKMS picker FastAPI sub-app.

    Does not require an AKMS source checkout: ``settings.akms_repo`` is
    optional and only consulted for the legacy plan when present.

    Raises:
        :class:`RuntimeError`: if the ``akms_nodes_gen`` package is not
            importable.
        :class:`FileNotFoundError`: if neither ``<akms_workspace>/batches.json``
            nor the legacy dev-time ``generation_plan.md`` is present, or no
            Zotero catalog is configured (both caught by ``main.py``'s
            degrade-gracefully mount wrapper).
    """
    try:
        from akms_nodes_gen.batch_picker.config import Paths
        from akms_nodes_gen.batch_picker.server import create_app
    except ImportError as e:
        raise RuntimeError(
            "akms_nodes_gen is not importable. Install the AKMS extra "
            "(`uv sync --extra akms`) so the batch picker package is available."
        ) from e

    plan_md = _resolve_plan_md(settings)
    paths = _build_paths(settings, Paths, plan_md=plan_md)
    app = create_app(paths)
    # Same error envelope as the rest of /api/* ({"error": {code, message,
    # detail}}), instead of FastAPI's bare {"detail": ...}.
    from ..errors import install_handlers

    install_handlers(app)

    workspace = Path(settings.akms_workspace).expanduser()
    if plan_md == workspace / _PLAN_CACHE_RELPATH:
        watcher = _BatchesWatcher(workspace, app.state.picker)

        @app.middleware("http")
        async def _refresh_plan(request: Request, call_next: Any) -> Any:
            watcher.refresh_if_changed()
            return await call_next(request)

    return app


class _BatchesWatcher:
    """Re-export the plan and reload the picker when ``batches.json`` changes.

    The upstream picker parses its plan once, in ``create_app``. Without this,
    batches created or edited under AKMS > Batches never reached the picker
    until the app restarted. The check is one ``stat`` per picker request.
    """

    def __init__(self, workspace: Path, repo: Any) -> None:
        self._workspace = workspace
        self._repo = repo
        self._lock = threading.Lock()
        self._signature = self._current_signature()

    def _current_signature(self) -> tuple[int, int] | None:
        try:
            st = (self._workspace / "batches.json").stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def refresh_if_changed(self) -> None:
        signature = self._current_signature()
        if signature is None or signature == self._signature:
            return
        with self._lock:
            if signature == self._signature:
                return
            try:
                _materialise_plan_from_batches(
                    self._workspace / "batches.json",
                    self._workspace / _PLAN_CACHE_RELPATH,
                )
                self._repo.load()
            except Exception as exc:  # noqa: BLE001 — keep serving the old plan
                logger.warning("picker plan refresh failed: %s", exc)
                return
            self._signature = signature


def _resolve_plan_md(settings: Settings) -> Path:
    """Pick the plan_md path the picker should read.

    Materialises the workspace ``batches.json`` into a derived legacy
    markdown file when present; otherwise returns the legacy dev-time
    source, which is only considered when an ``akms_repo`` checkout is
    present. Raises :class:`FileNotFoundError` when neither source exists
    so the caller's degrade-gracefully wrapper can log + skip.
    """
    workspace = Path(settings.akms_workspace).expanduser()
    batches_json = workspace / "batches.json"
    repo = _akms_repo_dir(settings)
    legacy_plan = repo / _LEGACY_PLAN_RELPATH if repo is not None else None

    if batches_json.exists():
        cache_path = workspace / _PLAN_CACHE_RELPATH
        try:
            _materialise_plan_from_batches(batches_json, cache_path)
        except Exception as exc:  # noqa: BLE001 — fall back rather than 500
            logger.warning(
                "batches.json -> generation_plan.md materialisation failed at %s: "
                "%s. Falling back to legacy plan_md.",
                cache_path,
                exc,
            )
        else:
            logger.info(
                "picker plan source: materialised from %s into %s",
                batches_json,
                cache_path,
            )
            return cache_path

    if legacy_plan is not None and legacy_plan.exists():
        logger.info("picker plan source: legacy %s", legacy_plan)
        return legacy_plan

    legacy_desc = (
        str(legacy_plan)
        if legacy_plan is not None
        else "a legacy generation_plan.md (no akms_repo checkout configured)"
    )
    raise FileNotFoundError(
        f"no picker data source: neither {batches_json} nor {legacy_desc} exists. "
        "Create at least one batch via /api/batches before mounting the picker."
    )


def _materialise_plan_from_batches(
    batches_json: Path, target: Path
) -> None:
    """Render ``batches.json`` into a legacy ``generation_plan.md`` text file.

    Local import for the bridge so this module stays cheap to import (the
    batches package is only needed when the workspace store is
    present).
    """
    from ..batches.compat import export_generation_plan
    from ..batches.store import BatchStore

    # The store loads from a directory (it appends batches.json itself).
    store = BatchStore.load(batches_json.parent)
    view = _picker_view(store, batches_json.parent / _PICKER_ID_MAP_RELPATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(export_generation_plan(view), encoding="utf-8")


def _load_id_map(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if _LEGACY_ID.match(str(v))}


def _picker_view(store: Any, id_map_path: Path) -> Any:
    """Return a copy of ``store`` in which every batch has a legacy plan id.

    ``export_generation_plan`` keeps only batches with an ``R<n>_B<m>`` id and
    a round, because that is all the upstream plan parser reads. A batch with
    a legacy id but no round gets the round from its id. Any other batch
    (``starter-placeholder``, or a slug minted from a title) gets a stable
    picker id ``R900_B<n>`` from the persistent map, and its workspace id is
    appended to the title so the picker shows which batch it is.
    """
    from ..batches.store import BatchStore

    id_map = _load_id_map(id_map_path)
    legacy_ids = {b.id for b in store.batches if _LEGACY_ID.match(b.id)}
    taken = legacy_ids | set(id_map.values())
    next_n = 1
    changed = False
    out = []
    for batch in store.batches:
        match = _LEGACY_ID.match(batch.id)
        if match:
            if batch.round is None:
                batch = batch.model_copy(update={"round": int(match["round"])})
            out.append(batch)
            continue
        picker_id = id_map.get(batch.id)
        if picker_id is None or picker_id in legacy_ids:
            while f"R{_WORKSPACE_ROUND}_B{next_n}" in taken:
                next_n += 1
            picker_id = f"R{_WORKSPACE_ROUND}_B{next_n}"
            taken.add(picker_id)
            id_map[batch.id] = picker_id
            changed = True
        out.append(
            batch.model_copy(
                update={
                    "id": picker_id,
                    "title": f"{batch.title} · {batch.id}",
                    "round": _WORKSPACE_ROUND,
                    "round_title": _WORKSPACE_ROUND_TITLE,
                    "round_theme": "",
                }
            )
        )
    if changed:
        id_map_path.parent.mkdir(parents=True, exist_ok=True)
        id_map_path.write_text(json.dumps(id_map, indent=2, sort_keys=True), encoding="utf-8")
    return BatchStore(batches=out)


def _build_paths(settings: Settings, PathsCls, *, plan_md: Path | None = None):  # type: ignore[no-untyped-def]
    """Construct an AKMS ``Paths`` instance from LogicLoom settings.

    LogicLoom-owned overrides:
      - ``workspace`` = the Logic-Loom ``akms_workspace`` (the picker's own
        state directory; upstream only echoes it from ``/api/config``).
      - ``plan_md`` from the caller (typically :func:`_resolve_plan_md`,
        which prefers ``<akms_workspace>/batches.json`` and falls back to
        the legacy dev-time source). If ``plan_md`` is ``None`` it defaults
        to the legacy location inside a present ``akms_repo`` checkout, else
        to the workspace's materialised plan cache.
      - ``state_file`` (``batch_assignments.json``), ``queries_file``
        (``saved_queries.json``), ``inputs_dir`` (NLM/Inputs), ``sources_dir``
        (AKMS_Sources/new) — runtime-read state, resolved via
        ``settings.akms_workspace`` so packaged builds don't need ``akms_repo``
        to read these.
      - ``zotsums_root`` / ``bbt_json`` via :func:`_resolve_catalog_paths`.
    """
    workspace = Path(settings.akms_workspace).expanduser()
    repo = _akms_repo_dir(settings)
    if plan_md is None:
        plan_md = (
            repo / _LEGACY_PLAN_RELPATH
            if repo is not None
            else workspace / _PLAN_CACHE_RELPATH
        )
    state_file = settings.akms_batch_state_path()
    queries_file = settings.akms_saved_queries_path()
    inputs_dir = settings.akms_nlm_inputs_dir()
    sources_dir = workspace / "AKMS_Sources" / "new"
    zotsums_root, bbt_json = _resolve_catalog_paths(settings)

    return PathsCls(
        workspace=workspace,
        plan_md=plan_md,
        bbt_json=bbt_json,
        zotsums_root=zotsums_root,
        state_file=state_file,
        queries_file=queries_file,
        inputs_dir=inputs_dir,
        sources_dir=sources_dir,
    )


# zsum's default output folder, which holds ``zsumbib.json`` and ``Papers/``.
# akms-nodes-gen uses the same default.
_ZSUM_DEFAULT_ROOT = Path("~") / "ZotSums"


def _resolve_catalog_paths(settings: Settings) -> tuple[Path, Path]:
    """Return ``(zotsums_root, bbt_json)`` for the picker's Zotero catalog.

    Precedence:
      1. ``settings.vault_path`` → ``(<vault>, <vault>/zsumbib.json)``.
      2. AKMS's own environment overrides, ``AKMS_ZOTSUMS_ROOT`` and
         ``AKMS_BBT_JSON`` (either one; the other derives from it).
      3. zsum's default output folder, ``~/ZotSums``. This deliberately
         replaces the vendored akms-nodes-gen 0.1.0 default, a cloud-synced
         folder on the AKMS author's machine whose ``open()`` can block on a
         not-yet-downloaded placeholder. A missing catalog is not fatal here:
         akms-nodes-gen starts with an empty catalog and a notice.
    """
    if settings.vault_path is not None:
        zotsums_root = Path(settings.vault_path).expanduser()
        return zotsums_root, zotsums_root / "zsumbib.json"

    env_root = os.environ.get("AKMS_ZOTSUMS_ROOT")
    env_bbt = os.environ.get("AKMS_BBT_JSON")
    if env_root or env_bbt:
        bbt_from_env = (
            Path(os.path.expandvars(env_bbt)).expanduser() if env_bbt else None
        )
        if env_root:
            zotsums_root = Path(os.path.expandvars(env_root)).expanduser()
        else:
            assert bbt_from_env is not None
            zotsums_root = bbt_from_env.parent
        bbt_json = bbt_from_env or (zotsums_root / "zsumbib.json")
        return zotsums_root, bbt_json

    zotsums_root = _ZSUM_DEFAULT_ROOT.expanduser()
    return zotsums_root, zotsums_root / "zsumbib.json"
