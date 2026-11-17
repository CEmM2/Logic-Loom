"""LogicLoom FastAPI façade."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from . import __version__
from .adapters.picker_adapter import build_picker_app
from .akms_workspace import bootstrap_akms_workspace
from .auth import LocalAuthMiddleware
from .assistant import bootstrap_assistant_workspace
from .config import Settings
from .errors import install_handlers
from .middleware import CorrelationIdMiddleware
from .routes import akms_assets as akms_assets_routes
from .routes import akms_dispatch as akms_dispatch_routes
from .routes import learn as learn_routes
from .routes import llm as llm_routes
from .routes import app as app_routes
from .routes import assistant_batches as assistant_batches_routes
from .routes import assistant_discover as assistant_discover_routes
from .routes import assistant_index as assistant_index_routes
from .routes import assistant_outputs as assistant_outputs_routes
from .routes import assistant_workflows as assistant_workflows_routes
from .routes import assistant_zotero_picker as assistant_zotero_picker_routes
from .routes import batches as batches_routes
from .routes import failure_memory as failure_memory_routes
from .routes import graph as graph_routes
from .routes import mechdsl as mechdsl_routes
from .routes import records as records_routes
from .routes import nlm as nlm_routes
from .routes import nodes as nodes_routes
from .routes import prompts as prompts_routes
from .routes import runs as runs_routes
from .routes import vault as vault_routes
from .routes import workflows as workflows_routes
from .routes import workspace as workspace_routes
from .routes import zsum as zsum_routes
from .runs.registry import init_registry

logger = logging.getLogger("logic_loom_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]


@asynccontextmanager
async def lifespan(_app):
    s = Settings.load()
    s.cache_path.mkdir(parents=True, exist_ok=True)
    # Host LLM provider wiring. Inject any configured completion API keys
    # into the sidecar env (lights up the LiteLLM 'akms' route + akms_learn's
    # gate) and register host agent-CLI providers (claude/codex/custom). Same
    # degrade-gracefully invariant — never block the sidecar coming up.
    try:
        from .learn_llm_providers import (
            inject_api_keys_into_env,
            register_host_llm_providers,
        )

        inject_api_keys_into_env(s)
        register_host_llm_providers(s)
        # All-consumer status log. Reports the effective provider choice
        # for each consumer so startup logs surface the active routing at a glance.
        logger.info(
            "host LLM provider status — learn: %s | zsum: %s | nodegen: %s",
            s.effective_llm_provider("learn"),
            s.effective_llm_provider("zsum"),
            s.effective_llm_provider("nodegen"),
        )
    except Exception as exc:
        logger.warning("host LLM provider wiring skipped: %s", exc)
    # Bootstrap AKMS workspace. CLAUDE.md degrade-gracefully invariant: a
    # workspace bootstrap failure must NEVER block the sidecar coming up.
    try:
        bootstrap_akms_workspace(s)
    except Exception as exc:
        logger.warning("akms_workspace bootstrap skipped: %s", exc)
    # First launch of a fresh install: the picker mount at import time ran before
    # the workspace existed. Same degrade-gracefully invariant.
    try:
        _mount_picker_after_bootstrap()
    except Exception as exc:  # noqa: BLE001
        logger.warning("picker mount retry skipped: %s", exc)
    # Bootstrap the Assistant workspace. Runs after
    # the AKMS bootstrap so the resolver's default-layout path
    # (<akms_workspace>/../Assistant-Workspace) can be materialised
    # alongside the just-created AKMS tree. Same degrade-gracefully
    # invariant — never block the sidecar coming up.
    try:
        bootstrap_assistant_workspace(s)
    except Exception as exc:
        logger.warning("assistant_workspace bootstrap skipped: %s", exc)
    init_registry(s.cache_path, s.zsum_repo_dir())
    yield


api = FastAPI(title="logic-loom-api", version=__version__, lifespan=lifespan)

# Local API auth (per-launch token + loopback Host allowlist). Added BEFORE
# CORSMiddleware so CORS wraps it: preflights are answered by CORS and never
# need the token, and a 401/421 still carries CORS headers so the webview can
# read it. It sits on the outer app, so the /api/picker sub-app is covered too.
api.add_middleware(LocalAuthMiddleware)

api.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    # PATCH is required by the batch-defaults endpoints
    # (PATCH /api/assistant/batches/{id}, PATCH /api/batches/{id}). It was
    # missing here, so WebKit's CORS preflight for those requests failed
    # with a bare "Load failed" and every default-workflow / prompt-profile
    # save silently broke. HEAD rounds out the simple-method set.
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-ID"],
)
api.add_middleware(CorrelationIdMiddleware)

install_handlers(api)

api.include_router(app_routes.router, prefix="/api/app", tags=["app"])
api.include_router(graph_routes.router, prefix="/api/graph", tags=["graph"])
api.include_router(vault_routes.router, prefix="/api/vault", tags=["vault"])
api.include_router(zsum_routes.router, prefix="/api/zsum", tags=["zsum"])
api.include_router(nlm_routes.router, prefix="/api/nlm", tags=["nlm"])
api.include_router(nodes_routes.router, prefix="/api/nodes", tags=["nodes"])

# Mount workspace-level migration routes. Same degrade-gracefully
# pattern — the route's only filesystem touch is on request,
# the mount itself is infallible.
try:
    api.include_router(
        workspace_routes.router,
        prefix="/api/workspace",
        tags=["workspace"],
    )
    logger.info("mounted Workspace routes at /api/workspace")
except Exception as e:
    logger.warning("Workspace routes mount skipped: %s", e)

# Mount Claude assets routes. CLAUDE.md degrade-gracefully invariant: if the
# bundled resources package is missing (core-only sidecar build), log a
# warning and skip — the routes return 4xx/5xx on access rather than crashing
# the app at startup. The actual import is cheap; we still guard the
# include_router call so any future eager resource-touch can't break startup.
try:
    api.include_router(
        akms_assets_routes.router,
        prefix="/api/akms/claude-assets",
        tags=["akms-assets"],
    )
    logger.info("mounted Claude assets routes at /api/akms/claude-assets")
except Exception as e:
    logger.warning("Claude assets routes mount skipped: %s", e)

# Mount Prompt Library routes. Same degrade-gracefully pattern as the
# Claude assets routes above — a failure logs and the rest of the app stays
# up.
try:
    api.include_router(
        prompts_routes.router,
        prefix="/api/prompts",
        tags=["prompts"],
    )
    logger.info("mounted Prompt Library routes at /api/prompts")
except Exception as e:
    logger.warning("Prompt Library routes mount skipped: %s", e)

# Mount Batch CRUD routes. Same degrade-gracefully pattern:
# the routes load lazily and only touch <akms_workspace>/batches.json on
# request, so the mount itself is essentially infallible — the try/except
# guards future eager imports that might land here.
try:
    api.include_router(
        batches_routes.router,
        prefix="/api/batches",
        tags=["batches"],
    )
    logger.info("mounted Batches routes at /api/batches")
except Exception as e:
    logger.warning("Batches routes mount skipped: %s", e)

# Mount Assistant batches routes. Same degrade-gracefully pattern as above — routes touch
# <assistant_workspace>/batches.json on request only, so the mount is
# essentially infallible. The send-to-akms endpoint additionally writes
# into <akms_workspace>/batches.json via the AKMS-bundle BatchStore.
try:
    api.include_router(
        assistant_batches_routes.router,
        prefix="/api/assistant/batches",
        tags=["assistant-batches"],
    )
    logger.info("mounted Assistant batches routes at /api/assistant/batches")
except Exception as e:
    logger.warning("Assistant batches routes mount skipped: %s", e)

# Mount Assistant discover routes. Provider clients
# only fire on request; the on-disk cache lives under
# <assistant_workspace>/discovery_cache/ so this mount is essentially
# infallible. Cache stats + clear endpoints live in the same router.
try:
    api.include_router(
        assistant_discover_routes.router,
        prefix="/api/assistant/discover",
        tags=["assistant-discover"],
    )
    logger.info("mounted Assistant discover routes at /api/assistant/discover")
except Exception as e:
    logger.warning("Assistant discover routes mount skipped: %s", e)

# Mount Assistant workflow library view. Filtered
# view over the shared workflow library by ASSISTANT_CATEGORIES. Same
# degrade-gracefully pattern.
try:
    api.include_router(
        assistant_workflows_routes.router,
        prefix="/api/assistant/workflows",
        tags=["assistant-workflows"],
    )
    logger.info("mounted Assistant workflows routes at /api/assistant/workflows")
except Exception as e:
    logger.warning("Assistant workflows routes mount skipped: %s", e)

# Mount Assistant outputs routes. Filtered view of
# the shared run registry for Assistant-family workflow_ids. Same
# degrade-gracefully pattern.
try:
    api.include_router(
        assistant_outputs_routes.router,
        prefix="/api/assistant/outputs",
        tags=["assistant-outputs"],
    )
    logger.info("mounted Assistant outputs routes at /api/assistant/outputs")
except Exception as e:
    logger.warning("Assistant outputs routes mount skipped: %s", e)

# Mount Assistant research-memory index routes.
# Two endpoints: POST /rebuild walks every workspace artefact, GET
# /status returns per-kind counts + last-rebuild timestamp. The
# lifecycle owner is a process-wide singleton; the routes touch
# <assistant_workspace>/index/fts.sqlite only on request, so the mount
# itself is essentially infallible.
try:
    api.include_router(
        assistant_index_routes.router,
        prefix="/api/assistant/index",
        tags=["assistant-index"],
    )
    logger.info("mounted Assistant index routes at /api/assistant/index")
except Exception as e:
    logger.warning("Assistant index routes mount skipped: %s", e)

# Mount Assistant Zotero picker routes. Searches a dedicated FTS5
# index over the user's full BBT JSON library plus the existing assistant
# FTS for already-processed paper cards. Same degrade-gracefully pattern
# as the routes above — the picker silently returns an empty result with
# a ``library_reason`` hint when the BBT export is missing.
try:
    api.include_router(
        assistant_zotero_picker_routes.router,
        prefix="/api/assistant/zotero",
        tags=["assistant-zotero-picker"],
    )
    logger.info("mounted Assistant Zotero picker routes at /api/assistant/zotero")
except Exception as e:  # noqa: BLE001 — preserve degrade-gracefully
    logger.warning("Assistant Zotero picker routes mount skipped: %s", e)

# Mount Workflow Library routes. Same degrade-gracefully
# pattern as the routes above: a failure logs and the rest of the app stays
# up. Routes load lazily, so the mount itself is essentially infallible.
try:
    api.include_router(
        workflows_routes.router,
        prefix="/api/workflows",
        tags=["workflows"],
    )
    logger.info("mounted Workflow Library routes at /api/workflows")
except Exception as e:
    logger.warning("Workflow Library routes mount skipped: %s", e)

# Mount Run routes (workflow runs, list, stream, cancel, rerun, output,
# effective-prompts). Same degrade-gracefully pattern.
try:
    api.include_router(
        runs_routes.router,
        prefix="/api/runs",
        tags=["runs"],
    )
    logger.info("mounted Run routes at /api/runs")
except Exception as e:
    logger.warning("Run routes mount skipped: %s", e)

# Mount AKMS dispatch routes. Same degrade-gracefully
# pattern as the other routers above. The routes themselves return 503 when
# the bundled `akms.node_extraction` workflow is missing (AKMS extras not
# present), but the mount itself never crashes startup — preflight,
# create-run, get-run, stream, and cancel all live on a single router so
# this is one try/except, not five.
try:
    api.include_router(
        akms_dispatch_routes.router,
        prefix="/api/akms/node-generation",
        tags=["akms-dispatch"],
    )
    logger.info("mounted AKMS dispatch routes at /api/akms/node-generation")
except Exception as e:
    logger.warning("AKMS dispatch routes mount skipped: %s", e)

# Mount Learn routes. The router is always mountable; handlers
# return 503 when the optional ``akms_learn`` plugin is absent so the app
# stays up in core-only mode. Same degrade-gracefully pattern as the other
# optional routers above.
try:
    api.include_router(
        learn_routes.router,
        prefix="/api/learn",
        tags=["learn"],
    )
    logger.info("mounted Learn routes at /api/learn")
except Exception as e:  # noqa: BLE001 — preserve degrade-gracefully
    logger.warning("Learn routes mount skipped: %s", e)

# Mount unified LLM provider routes. Generalises /api/learn/providers
# to cover all consumers (learn/zsum/nodegen). The /api/learn/providers/reload
# path shares the same reload side-effect (_reload_core) but keeps its frozen
# legacy `selected` (raw learn_llm_provider); /api/llm reports the effective choice.
# Same degrade-gracefully pattern: a mount failure logs and the rest of the
# app stays up.
try:
    api.include_router(
        llm_routes.router,
        prefix="/api/llm",
        tags=["llm"],
    )
    logger.info("mounted LLM routes at /api/llm")
except Exception as e:  # noqa: BLE001 — preserve degrade-gracefully
    logger.warning("LLM routes mount skipped: %s", e)

# Mount AKMS batch picker under /api/picker. No AKMS source checkout is needed
# (akms_repo is an optional dev override). If the akms_nodes_gen package is
# missing, the workspace has no batches.json, or no Zotero catalog is
# configured, log a warning and continue — the rest of the app still works for
# vault/zsum/settings flows. The picker route will 404 until config is fixed.
#
# The picker's plan source is <akms_workspace>/batches.json, which the workspace
# bootstrap seeds. That bootstrap runs in the lifespan, after this module-level
# mount, so on the very first launch of a fresh install the file does not exist
# yet and this attempt is skipped. The lifespan therefore retries the mount once
# the workspace exists (see `_mount_picker_after_bootstrap`); without the retry
# the picker stayed unavailable until the app was restarted.
PICKER_PREFIX = "/api/picker"
PICKER_TAG = "akms-picker"


def _openapi_with_picker() -> dict:
    """The app's OpenAPI schema plus the mounted picker's routes.

    A mounted sub-app is invisible to FastAPI's schema, so the picker's 18
    routes were missing from /openapi.json. Its paths are merged in under
    ``/api/picker`` with the ``akms-picker`` tag; component schemas are
    added where the name is free.
    """
    if api.openapi_schema:
        return api.openapi_schema
    schema = FastAPI.openapi(api)
    for route in api.routes:
        if isinstance(route, Mount) and route.path == PICKER_PREFIX and isinstance(route.app, FastAPI):
            sub = route.app.openapi()
            for path, item in sub.get("paths", {}).items():
                for op in item.values():
                    if isinstance(op, dict):
                        op["tags"] = [PICKER_TAG]
                        if "operationId" in op:
                            op["operationId"] = f"picker_{op['operationId']}"
                schema.setdefault("paths", {})[PICKER_PREFIX + path] = item
            components = schema.setdefault("components", {}).setdefault("schemas", {})
            for name, component in sub.get("components", {}).get("schemas", {}).items():
                components.setdefault(name, component)
    api.openapi_schema = schema
    return schema


api.openapi = _openapi_with_picker  # type: ignore[method-assign]


def _mount_picker(*, retry_pending: bool = False) -> bool:
    """Mount the batch picker; log and return False when it cannot be built.

    ``retry_pending`` marks the import-time attempt, which normally runs before
    the lifespan has bootstrapped the workspace: a missing file there is
    expected and is retried after bootstrap, so it logs at INFO. Only a
    failure that survives the retry is a WARNING (v0.3.1 QA: the old warning on
    every start was noise).
    """
    try:
        api.mount(PICKER_PREFIX, build_picker_app(Settings.load()))
        api.openapi_schema = None  # rebuild with the picker's routes
    except (RuntimeError, FileNotFoundError, OSError) as e:
        # Missing AKMS / missing zsumbib.json / unreadable state file: log and
        # continue. The picker namespace will 404 until config is fixed.
        if retry_pending and isinstance(e, FileNotFoundError):
            logger.info("picker not mounted yet, retrying after workspace bootstrap: %s", e)
        else:
            logger.warning("picker mount skipped: %s", e)
        return False
    logger.info("mounted AKMS batch picker at /api/picker")
    return True


_picker_mounted = _mount_picker(retry_pending=True)


def _mount_picker_after_bootstrap() -> None:
    """Retry a skipped picker mount once the workspace bootstrap has run."""
    global _picker_mounted
    if not _picker_mounted:
        _picker_mounted = _mount_picker()


# Mount MechDSL executable-bridge routes.
# Same degrade-gracefully pattern: the routes lazily import the optional
# `compmech_reference_pack` companion adapter (+ `mechdsl` core, + `akms_learn`)
# on request and return 503 when any is missing, so the mount itself never
# crashes startup — the sidecar builds and runs without the 'mechdsl' extra.
try:
    api.include_router(
        mechdsl_routes.router,
        prefix="/api/mechdsl",
        tags=["mechdsl"],
    )
    logger.info("mounted MechDSL routes at /api/mechdsl")
except Exception as e:
    logger.warning("MechDSL routes mount skipped: %s", e)

# Mount akms-records routes (read-only browse of [[records_stores]]). Same
# degrade-gracefully pattern: the adapter imports the optional `akms_records`
# package lazily, /status is always 200 and every per-store endpoint returns
# 503 without it, so the sidecar runs without the 'records' extra.
try:
    api.include_router(
        records_routes.router,
        prefix="/api/records",
        tags=["records"],
    )
    logger.info("mounted akms-records routes at /api/records")
except Exception as e:  # noqa: BLE001 — preserve degrade-gracefully
    logger.warning("akms-records routes mount skipped: %s", e)

# Mount Failure Memory routes. Same
# degrade-gracefully pattern as the Learn and MechDSL routers: the module
# imports cleanly whether or not the optional `akms-failure-memory` package is
# installed, every handler probes for it at request time, and GET /projects
# stays 200 so the workspace gate can render an install hint instead of an
# error. The default surface is READ-ONLY: the record/refresh/compile endpoints
# return a typed disabled response unless failure_memory_mode = "operator" is
# set in config, and every write goes through the operator adapter.
try:
    api.include_router(
        failure_memory_routes.router,
        prefix="/api/failure-memory",
        tags=["failure-memory"],
    )
    logger.info("mounted Failure Memory routes at /api/failure-memory")
except Exception as e:  # noqa: BLE001 — preserve degrade-gracefully
    logger.warning("Failure Memory routes mount skipped: %s", e)
