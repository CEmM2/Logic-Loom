"""``/api/akms/claude-assets/*`` routes.

HTTP surface for the user-editable Claude assets directory and the per-run
runtime materializer. Errors are surfaced as 400 ``HTTPException``s so the
``errors.install_handlers`` envelope wraps them with a uniform JSON shape —
the route layer never raises a 500 on user-supplied data.

Route inventory (7 routes):

* ``GET  /status``               — installed state + manifest snapshot
* ``POST /install``              — install or reset the editable mirror
* ``GET  /file?path=…``          — read a single asset (user copy preferred)
* ``PUT  /file?path=…``          — write the user copy of a single asset
* ``POST /reset?path=…``         — restore a single asset from the bundled copy
* ``POST /materialize-runtime``  — materialize a fresh per-run runtime tree
* ``GET  /folder``               — absolute path for system-level reveal
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..akms_claude_assets import (
    AkmsBundledAssetsMissing,
    asset_status,
    bundled_root,
    install_editable_assets,
    read_user_or_bundled,
    reset_user_asset,
    safe_asset_path,
    user_assets_dir,
    write_user_asset,
)
from ..akms_claude_runtime import AkmsAssetsUnavailable, materialize_runtime_workspace

router = APIRouter()


class FileBody(BaseModel):
    """Body for ``PUT /file``: the new content of a user-editable asset."""

    content: str


@router.get("/status")
def status() -> dict[str, Any]:
    """Return the install state of the user-editable assets directory."""
    return asset_status()


@router.post("/install")
def install(overwrite: bool = False) -> dict[str, Any]:
    """Install (or reset) the user-editable mirror.

    When ``overwrite=true`` the previous user tree is moved to
    ``claude-assets.backup/`` before the bundled tree is copied in. The
    response carries the active path and the backup path so the UI can warn
    the user and offer to reveal the backup.
    """
    try:
        path = install_editable_assets(overwrite=overwrite)
    except AkmsBundledAssetsMissing as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    backup = path.with_name("claude-assets.backup")
    return {
        "path": str(path),
        "overwrite": overwrite,
        "backup_path": str(backup),
        "backup_exists": backup.exists(),
        "warning": (
            "Existing user edits were moved to backup_path. Only the most "
            "recent reset is recoverable — the next reset will delete this "
            "backup."
        ) if overwrite and backup.exists() else None,
    }


@router.get("/file")
def read_file(path: str, default: bool = False) -> dict[str, Any]:
    """Read a single asset.

    By default returns the user-edited copy if present, falling back to the
    bundled default. Pass ``default=true`` to force-read the bundled default
    even when a user copy exists (used by the "Show default" UI affordance).
    """
    try:
        if default:
            rel = safe_asset_path(path)
            try:
                bundled_path = bundled_root().joinpath(*rel.parts)
            except AkmsBundledAssetsMissing as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            if not bundled_path.is_file():
                raise HTTPException(
                    status_code=404,
                    detail=f"bundled asset not found: {path}",
                )
            content = bundled_path.read_text(encoding="utf-8")
        else:
            content = read_user_or_bundled(path)
    except ValueError as e:  # bad path
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"path": path, "content": content, "source": "bundled" if default else "user_or_bundled"}


@router.put("/file")
def write_file(path: str, body: FileBody) -> dict[str, Any]:
    """Write the user copy of a single asset and mark the manifest dirty."""
    try:
        written = write_user_asset(path, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"path": str(written)}


@router.post("/reset")
def reset(path: str) -> dict[str, Any]:
    """Restore a single user-edited asset from the bundled defaults."""
    try:
        reset_user_asset(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "reset", "path": path}


@router.post("/materialize-runtime")
def materialize_runtime() -> dict[str, Any]:
    """Materialize a fresh per-run claude-runtime workspace."""
    try:
        path = materialize_runtime_workspace(force=True)
    except AkmsAssetsUnavailable as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"path": str(path)}


@router.get("/folder")
def folder() -> dict[str, Any]:
    """Return the absolute user_assets_dir path for a system-level reveal."""
    path = user_assets_dir()
    return {"path": str(path), "exists": path.exists()}
