"""User-editable copy of bundled Claude assets.

This module is the read/write contract for the user-editable mirror of the
AKMS-Claude assets that ship inside the sidecar wheel under
``logic_loom_api.resources.akms_claude``.

User edits are sacred:

* Edits live in ``~/.config/logic-loom/claude-assets/`` (cross-platform per
  :func:`user_config_dir`). They are **never** stored inside the app bundle.
* :func:`install_editable_assets` with ``overwrite=True`` always moves the
  existing tree to ``claude-assets.backup/`` before copying — resetting must
  never destroy edits silently. The audit guarantee is exercised by
  ``tests/akms/test_claude_assets_reset_backup.py``.
* :func:`safe_asset_path` is the single security choke point: absolute paths,
  ``..`` traversal segments, empty paths, and any first-segment outside
  ``{".claude", "scripts"}`` are rejected.

Bundled defaults are read via :mod:`importlib.resources`; do **not** compute
paths via ``Path(__file__).parent`` because the sidecar is shipped as a
PyInstaller-frozen binary in production.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Any

# Anchor for ``importlib.resources`` reads. The wheel's ``force-include``
# guarantees this package exists in both editable installs and built wheels.
RESOURCE_PACKAGE = "logic_loom_api.resources.akms_claude"

# Exhaustive whitelist of first-segment names that may appear in an asset path.
# Treat this tuple as the contract — do not widen it without updating
# :func:`safe_asset_path` and the route layer in tandem.
ASSET_ROOTS: tuple[str, ...] = (".claude", "scripts")

MANIFEST_NAME = "manifest.json"


def user_config_dir() -> Path:
    """Return the per-user config directory for Logic-Loom.

    Cross-platform per §6.1 of the bundling guide:

    * Windows: ``%APPDATA%/Logic-Loom`` (Roaming) — falls back to
      ``~/AppData/Roaming/Logic-Loom`` if ``APPDATA`` is unset.
    * Linux/macOS: ``$XDG_CONFIG_HOME/logic-loom`` — falls back to
      ``~/.config/logic-loom`` if ``XDG_CONFIG_HOME`` is unset.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return base / "Logic-Loom"
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "logic-loom"


def user_assets_dir() -> Path:
    """Return the absolute path to the user-editable Claude assets tree."""
    return user_config_dir() / "claude-assets"


class AkmsBundledAssetsMissing(RuntimeError):
    """Raised when ``logic_loom_api.resources.akms_claude`` is not vendored.

    Surfaces from :func:`install_editable_assets` so the UI can render a
    targeted message instead of a generic 500.
    """


def bundled_root():
    """Return the bundled assets Traversable, or raise :class:`AkmsBundledAssetsMissing`.

    The AKMS Claude assets only ship in the wheel when
    ``scripts/sync_akms_claude_assets.py`` ran before the build (it copies them
    out of the installed ``akms`` package). Dev sidecars built without that
    step won't have the resources package; callers must catch
    :class:`AkmsBundledAssetsMissing`.
    """
    try:
        return resources.files(RESOURCE_PACKAGE)
    except (ModuleNotFoundError, FileNotFoundError) as e:
        raise AkmsBundledAssetsMissing(
            "AKMS Claude bundled defaults are not vendored in this Logic-Loom "
            "build (logic_loom_api.resources.akms_claude is missing). Run "
            "scripts/sync_akms_claude_assets.py with the akms extra installed "
            "and rebuild the sidecar."
        ) from e


def install_editable_assets(*, overwrite: bool = False) -> Path:
    """Materialise the user-editable Claude assets directory.

    Behaviour:

    * If ``user_assets_dir()`` does not exist, the bundled tree is copied into
      it and a fresh ``manifest.json`` is written with ``user_modified=False``.
    * If it exists and ``overwrite=False`` (default), the call is a no-op and
      the existing path is returned untouched.
    * If it exists and ``overwrite=True``, the existing tree is first **moved**
      to a sibling ``claude-assets.backup/`` directory (any prior
      ``claude-assets.backup`` is **removed first**) and then the bundled tree
      is copied in fresh. This is the "Reset all to bundled defaults" branch.

    Audit guarantee:

        After ``install_editable_assets(overwrite=True)``, the immediately
        previous user tree — including any user-created marker files — is
        recoverable in its entirety from ``claude-assets.backup/`` (a sibling
        of ``user_assets_dir()``). The previous tree is **moved**, not copied,
        so this function never doubles disk usage on reset.

        Only the **single most recent** reset is recoverable: a subsequent
        reset deletes the prior backup before staging a new one. The UI layer
        is responsible for warning the user before calling with
        ``overwrite=True`` and surfacing the backup path in the route response
        so the user can manually copy files out before the next reset.

        Edits are therefore never silently destroyed by a single reset, but
        repeated resets clobber the backup. Persist anything irreplaceable
        outside ``user_assets_dir()``.
    """
    target = user_assets_dir()
    if target.exists() and not overwrite:
        return target
    if target.exists():
        backup = target.with_name("claude-assets.backup")
        if backup.exists():
            shutil.rmtree(backup)
        # Move (not copy) so a marker file inside the old tree is recoverable
        # from the backup but no longer lives at the original location.
        shutil.move(str(target), str(backup))
    target.mkdir(parents=True, exist_ok=True)
    root = bundled_root()
    for name in ASSET_ROOTS:
        src = root.joinpath(name)
        if src.is_dir():
            shutil.copytree(str(src), target / name)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_from": "bundled",
        "user_modified": False,
    }
    (target / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


def safe_asset_path(relative_path: str) -> Path:
    """Validate a user-supplied relative asset path and return it as a Path.

    Rejects (with :class:`ValueError`):

    * Empty strings or paths with no parts.
    * Absolute paths (POSIX or Windows-style).
    * Any segment equal to ``..`` (traversal).
    * Paths whose first segment is not in :data:`ASSET_ROOTS`.

    This is the single security choke point for path-style query parameters
    accepted by ``/api/akms/claude-assets/*``, so its tests must keep 100%
    branch coverage.
    """
    if not relative_path:
        raise ValueError("empty asset path")
    rel = Path(relative_path)
    # Reject paths Path() considers absolute on the current OS, AND any
    # POSIX-style root-prefixed path. The latter clause is required on
    # Windows where Path("/etc/passwd").is_absolute() returns False
    # because pathlib only treats drive-rooted paths as absolute. The
    # security check must be platform-independent.
    if rel.is_absolute() or relative_path.startswith(("/", "\\")):
        raise ValueError(f"unsafe asset path (absolute): {relative_path}")
    if not rel.parts:
        raise ValueError("empty asset path")
    if ".." in rel.parts:
        raise ValueError(f"unsafe asset path (traversal): {relative_path}")
    if rel.parts[0] not in ASSET_ROOTS:
        raise ValueError(
            f"path must start with one of {ASSET_ROOTS!r}: {relative_path}"
        )
    return rel


def read_user_or_bundled(relative_path: str) -> str:
    """Read an asset, preferring the user-edited copy when present.

    The relative path is validated by :func:`safe_asset_path` first. If the
    user has a file at ``user_assets_dir() / rel``, that text is returned;
    otherwise the bundled copy under :data:`RESOURCE_PACKAGE` is read.
    """
    rel = safe_asset_path(relative_path)
    user_path = user_assets_dir() / rel
    if user_path.exists():
        return user_path.read_text(encoding="utf-8")
    bundled_path = bundled_root().joinpath(*rel.parts)
    return bundled_path.read_text(encoding="utf-8")


def write_user_asset(relative_path: str, content: str) -> Path:
    """Write content into the user-editable mirror and mark it modified.

    Creates any missing parent directories. Always flips the manifest's
    ``user_modified`` flag to True via :func:`mark_user_modified`.
    """
    rel = safe_asset_path(relative_path)
    path = user_assets_dir() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    mark_user_modified()
    return path


def reset_user_asset(relative_path: str) -> None:
    """Restore a single user-edited file from the bundled defaults.

    Per-file granular reset (vs. the whole-tree reset in
    :func:`install_editable_assets`). Raises :class:`FileNotFoundError` if
    there is no bundled counterpart at the requested path. Flips
    ``user_modified`` to True because the user explicitly took an action.
    """
    rel = safe_asset_path(relative_path)
    bundled = bundled_root().joinpath(*rel.parts)
    target = user_assets_dir() / rel
    if not bundled.is_file():
        raise FileNotFoundError(f"bundled asset not found: {relative_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
    mark_user_modified()


def mark_user_modified() -> None:
    """Flip ``user_modified`` to True in the user manifest.

    Idempotent. If the manifest is missing or unreadable, a fresh one is
    written.
    """
    manifest_path = user_assets_dir() / MANIFEST_NAME
    data: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data["user_modified"] = True
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def asset_status() -> dict[str, Any]:
    """Return the current install state of the user-editable assets dir."""
    target = user_assets_dir()
    manifest_path = target / MANIFEST_NAME
    manifest: dict[str, Any] | None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None
    else:
        manifest = None
    return {
        "installed": target.exists(),
        "path": str(target),
        "manifest": manifest,
    }
