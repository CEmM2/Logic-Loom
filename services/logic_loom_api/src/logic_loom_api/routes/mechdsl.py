"""``/api/mechdsl/*`` routes — the MechDSL executable-bridge plugin.

Mirrors the AKMS integration pattern already in this codebase:
  - ``routes/app.py:akms_status`` for the try-import availability probe,
  - the degrade-gracefully router mount in ``main.py``,
  - ``routes/vault.py:_safe_subpath`` for path-traversal-guarded serving.

The plugin is **optional**. When the ``compmech_reference_pack`` companion
adapter (and the ``mechdsl`` core it wraps) are not installed, ``/status``
reports ``available=False`` and ``/compile`` / ``/artifacts`` return 503 — the
sidecar still boots. Taichi is never imported here: emit/transpile are
Taichi-free; only ``run_verify=True`` pays the cost, inside the adapter.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .. import runtime_install
from ..config import Settings
from ..deps import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)

_ARTIFACT_SUBDIR = "mechdsl"
_RESULT_FILENAME = "result.json"
_EMITTED_FILENAME = "emitted_source.py"


def _mechdsl_cache_root(settings: Settings) -> Path:
    """Root for cached MechDSL compile artifacts: ``<cache_path>/mechdsl/``."""
    return settings.cache_path.expanduser() / _ARTIFACT_SUBDIR


def _safe_artifact_dir(cache_root: Path, artifact_id: str) -> Path:
    """Resolve ``<cache_root>/<artifact_id>`` and forbid traversal.

    Mirrors ``routes/vault.py:_safe_subpath``: resolve symlinks first, then
    require the candidate to live under the cache root.
    """
    candidate = (cache_root / artifact_id).resolve()
    root = cache_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal blocked")
    return candidate


def _capabilities() -> dict[str, Any] | None:
    """Tier-1 capability descriptor, or None if mechdsl is unavailable."""
    try:
        from mechdsl.integration import capabilities

        return capabilities()
    except Exception:
        return None


@router.get("/status")
def mechdsl_status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Report whether the MechDSL executable-bridge plugin is available.

    Shape: ``{available, package_installed, mechdsl_installed, capabilities,
    reason}``. ``available`` is true only when both the companion adapter and
    the mechdsl core import cleanly.
    """
    try:
        import compmech_reference_pack  # noqa: F401

        package_installed = True
    except ImportError:
        package_installed = False
    try:
        import mechdsl  # noqa: F401

        mechdsl_installed = True
    except ImportError:
        mechdsl_installed = False

    available = package_installed and mechdsl_installed
    capabilities = _capabilities() if available else None
    reason: str | None = None
    if not package_installed:
        reason = (
            "compmech_reference_pack is not installed (install the 'mechdsl' extra)"
        )
    elif not mechdsl_installed:
        reason = "mechdsl core is not installed (install the 'mechdsl' extra)"

    # Taichi backs the `verify` action only; emit/transpile are Taichi-free and
    # ship frozen. Taichi is side-installed on demand (see /runtime below), so
    # verify is available only when the emit bridge is up AND taichi imports AND
    # this platform has a taichi 1.7.4 wheel.
    platform_supported, platform_reason = runtime_install.platform_supported()
    taichi_avail = runtime_install.taichi_available()
    return {
        "available": available,
        "package_installed": package_installed,
        "mechdsl_installed": mechdsl_installed,
        "capabilities": capabilities,
        "reason": reason,
        "taichi_available": taichi_avail,
        "verify_available": available and taichi_avail and platform_supported,
        "platform_supported": platform_supported,
        "platform_reason": platform_reason,
    }


@router.get("/runtime")
def mechdsl_runtime(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Taichi runtime status: availability, platform support, install progress.

    Drives the MechDSL Overview "Install taichi" button. ``install_status`` is
    one of ``idle | installing | installed | failed``; ``log`` is the tail of
    the installer output for in-app progress.
    """
    return runtime_install.runtime_status(settings)


@router.post("/runtime/install-taichi")
def mechdsl_install_taichi(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Install the pinned Taichi wheel to unblock MechDSL ``verify``.

    Non-blocking: starts a background install and returns the runtime status
    immediately (poll ``GET /runtime``). 412 on platforms with no taichi 1.7.4
    wheel (e.g. macOS x86_64). A second call while installing is a no-op.
    """
    supported, reason = runtime_install.platform_supported()
    if not supported:
        raise HTTPException(status.HTTP_412_PRECONDITION_FAILED, detail=reason)
    return runtime_install.start_taichi_install(settings)


class CompileRequest(BaseModel):
    """Body for ``POST /api/mechdsl/compile``."""

    node_id: str | None = None
    graph_slice: dict[str, Any] | None = None
    run_verify: bool = False


def _build_artifact_id(result: dict[str, Any]) -> str:
    """Deterministic 16-hex content hash over the build result.

    Content-addressed so re-compiling the same node is idempotent (same
    artifact dir). Hashes only the load-bearing emit fields, not warnings.
    """
    payload = json.dumps(
        {
            "emitted_source": result.get("emitted_source"),
            "transpiled": result.get("transpiled"),
            "normalized_input": result.get("normalized_input"),
            "provenance": result.get("provenance"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _write_artifact(cache_root: Path, result: dict[str, Any]) -> str:
    """Persist a ``build_executable`` result under ``<cache_root>/<id>/``.

    Returns the artifact_id. Pure filesystem — unit-testable without the
    plugin installed (see ``test_mechdsl.py``).
    """
    artifact_id = _build_artifact_id(result)
    art_dir = _safe_artifact_dir(cache_root, artifact_id)
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / _RESULT_FILENAME).write_text(json.dumps(result, indent=2, default=str))
    emitted = result.get("emitted_source")
    if isinstance(emitted, str):
        (art_dir / _EMITTED_FILENAME).write_text(emitted)
    return artifact_id


def _excerpt_from_compiled(compiled: Any, node_id: str | None) -> dict[str, Any]:
    """Map an ``akms_learn`` CompileResult/LSP into a compmech adapter excerpt.

    The adapter's ``_select_node`` accepts either a top-level
    ``{node_id, extracted}`` excerpt or a ``{nodes: [...]}`` list, so we pass
    the packet's nodes through and let it pick. Defensive on shape (CompileResult
    with ``.packet`` vs a raw packet; pydantic ``model_dump`` vs dict) because
    the ``akms_learn`` request/LSP surface is owned by the Learn-tab work.
    """
    packet = getattr(compiled, "packet", compiled)
    data = packet.model_dump() if hasattr(packet, "model_dump") else dict(packet)
    body = data.get("body", data) if isinstance(data, dict) else {}
    nodes = body.get("nodes", []) if isinstance(body, dict) else []
    excerpt: dict[str, Any] = {"nodes": nodes}
    if node_id is not None:
        excerpt["node_id"] = node_id
    refs = body.get("references") if isinstance(body, dict) else None
    if refs:
        excerpt["references"] = refs
    return excerpt


@router.post("/compile")
def mechdsl_compile(
    payload: CompileRequest, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    """Compile an AKMS node / graph-slice into a MechDSL executable artifact.

    Flow: ``akms_learn.compile_learning_source`` → LSP excerpt →
    ``MechDSLRunner().build_executable(excerpt, {run_verify})`` → cache under
    ``<cache_path>/mechdsl/<artifact_id>/``. Emit-only unless
    ``run_verify=True`` (the only Taichi-paying branch).

    Degrade-gracefully: 503 when the companion adapter / ``mechdsl`` /
    ``akms_learn`` are absent (the latter is wired by the Learn-tab work).
    """
    if payload.node_id is None and payload.graph_slice is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="provide node_id or graph_slice"
        )

    # --- lazy imports: the dependency seam ----------------------------------
    # compmech_reference_pack + mechdsl ship via the 'mechdsl' extra; akms_learn
    # is wired separately by the Learn-tab work. Any missing piece → 503, never
    # a startup crash.
    try:
        from akms_learn import compile_learning_source
        from compmech_reference_pack.adapters.mechdsl_runner import MechDSLRunner
    except ImportError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"MechDSL plugin unavailable: {exc}",
        ) from exc

    graph_path = settings.graph_cache_path()
    if payload.graph_slice is None and not graph_path.exists():
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED,
            detail=f"AKMS graph not built at {graph_path}",
        )

    request: dict[str, Any] = {"node_ids": [payload.node_id]} if payload.node_id else {}
    try:
        compiled = compile_learning_source(
            request,
            graph_path=str(graph_path) if payload.graph_slice is None else None,
            graph_slice=payload.graph_slice,
        )
        excerpt = _excerpt_from_compiled(compiled, payload.node_id)
        result = MechDSLRunner().build_executable(
            excerpt, options={"run_verify": payload.run_verify}
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("mechdsl compile failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"compile failed: {type(exc).__name__}: {exc}",
        ) from exc

    artifact_id = _write_artifact(_mechdsl_cache_root(settings), result)
    return {
        "artifact_id": artifact_id,
        "status": result.get("status"),
        "emitted_source_ref": f"/api/mechdsl/artifacts/{artifact_id}",
        "vv_plan": result.get("vv_plan"),
        "provenance": result.get("provenance"),
        "warnings": result.get("warnings", []),
    }


@router.get("/artifacts/{artifact_id}")
def mechdsl_artifact(
    artifact_id: str, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    """Return a cached compile artifact's ``result.json`` (traversal-guarded)."""
    cache_root = _mechdsl_cache_root(settings)
    result_file = _safe_artifact_dir(cache_root, artifact_id) / _RESULT_FILENAME
    if not result_file.is_file():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"unknown artifact: {artifact_id}"
        )
    return json.loads(result_file.read_text())
