"""/api/nodes/* — AKMS node file browsing, editor launch, and promotion."""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, BinaryIO, Callable

import frontmatter  # type: ignore[import-not-found,import-untyped]

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..adapters import graph_adapter
from ..config import Settings
from ..deps import get_settings
from ..safe_paths import resolve_within, split_command

logger = logging.getLogger(__name__)

router = APIRouter()

_FM_FENCE = re.compile(r"^---\s*$")

# Allowlist for a filename-safe node id. A copy target is only ever
# ``<generated_nodes>/<copy_id>.md``, so the id must contain no path separators,
# no parent refs, and no other characters that could alter the write target.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_ALL_DOTS = re.compile(r"^\.+$")


def _safe_copy_id(original_id: str) -> str | None:
    """Return ``f"{original_id}-copy"`` iff *original_id* is filename-safe, else None.

    The node id is untrusted frontmatter. We reject (rather than slugify) so the
    copy's id stays a faithful, predictable derivative of the source id and any
    surprising input surfaces as a clean 400 instead of a silently-mangled file.
    Any all-dot id (``.``, ``..``, ``...`` …) is rejected explicitly — they
    match the char allowlist but are unsafe / meaningless path components.
    """
    candidate = str(original_id).strip()
    if candidate == "" or _ALL_DOTS.match(candidate):
        return None
    if not _SAFE_ID.match(candidate):
        return None
    return f"{candidate}-copy"


class _CopyWriteError(Exception):
    """Wraps a failure while streaming the copy body.

    Distinct from create-time errors (``FileExistsError`` / ``OSError``) so
    ``copy_to_vault`` can map a write failure to its own 500 without confusing it
    with a symlinked-directory rejection (ELOOP/ENOTDIR → 400).
    """


def _exclusive_write_under_root(
    root: Path,
    rel_dir_parts: tuple[str, ...],
    filename: str,
    write_fn: Callable[[BinaryIO], None],
) -> None:
    """Exclusively create ``root/<rel_dir_parts…>/filename`` and call ``write_fn``
    with its open binary handle to populate it.

    Raises ``FileExistsError`` if the target already exists, ``OSError``
    (``ELOOP`` / ``ENOTDIR``) if any directory component is a symlink, and
    ``_CopyWriteError`` if ``write_fn`` fails — in which case the partial file is
    unlinked (best-effort) first so the copy stays retryable.

    Parent-directory TOCTOU hardening: opening the
    target directory by its full path only ``O_NOFOLLOW``-guards the FINAL
    component — an *ancestor* (e.g. ``akms_workspace``) swapped for a symlink
    would still be followed — AND a path-based cleanup unlink could be redirected
    through a swapped ``generated_nodes`` to delete an outside file. So we pin the
    WHOLE chain by fd and keep the deepest directory fd alive across the write:
    open *root* with ``O_DIRECTORY | O_NOFOLLOW``, ``openat`` each of
    *rel_dir_parts* relative to the prior dir fd with ``O_DIRECTORY | O_NOFOLLOW``,
    create *filename* relative to the deepest dir fd with
    ``O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW``, and — on write failure — unlink
    via ``os.unlink(filename, dir_fd=deepest)``. Every operation (create, write,
    cleanup) is pinned by inode, so a mid-race swap of ``generated_nodes`` or an
    ancestor up to *root* is refused and can never redirect the cleanup. *root*
    and its own ancestors are the trust boundary (the user's workspace); closing
    above that would need the non-portable ``openat2(RESOLVE_BENEATH)``.

    Windows lacks ``dir_fd`` support, so it falls back to an exclusive ``xb``
    create + path-based cleanup (symlink creation there is privileged).
    """
    if not (
        os.open in os.supports_dir_fd
        # os.unlink is also called with dir_fd during cleanup; a platform that
        # supports dir_fd for open but not unlink would raise NotImplementedError
        # (NOT an OSError) mid-cleanup and mask the real error — so require both.
        and os.unlink in os.supports_dir_fd
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
    ):
        target_dir = root
        for part in rel_dir_parts:
            target_dir = target_dir / part
        target = target_dir / filename
        f = target.open("xb")  # FileExistsError → caller 409
        try:
            # Guard the WHOLE lifecycle incl. close(): a buffered flush at close
            # can surface ENOSPC/EIO AFTER write_fn returns, which must still
            # unlink the partial file rather than leave a corrupt 409-stub.
            try:
                write_fn(f)
            finally:
                f.close()
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise _CopyWriteError(str(exc)) from exc
        return

    open_fds: list[int] = []
    try:
        cur = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        open_fds.append(cur)
        for part in rel_dir_parts:
            cur = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur
            )
            open_fds.append(cur)
        deepest = cur
        fd = os.open(
            filename,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o644,
            dir_fd=deepest,
        )
        f = os.fdopen(fd, "wb")
        try:
            # Guard write AND close together: a buffered flush at close() can
            # surface ENOSPC/EIO AFTER write_fn returns; that must also trigger
            # the pinned cleanup, not leave a corrupt partial behind.
            try:
                write_fn(f)
            finally:
                f.close()
        except Exception as exc:
            # Pinned cleanup: unlink relative to the SAME directory fd, so a
            # concurrent swap of generated_nodes cannot redirect it through a
            # symlink and delete an outside file of the same name.
            try:
                os.unlink(filename, dir_fd=deepest)
            except OSError:
                pass
            raise _CopyWriteError(str(exc)) from exc
    finally:
        for f_ in open_fds:
            os.close(f_)


def _is_established_path(p: Path) -> bool:
    """Return True when *p* is under the shipped established library root.

    Established (Tier-A) nodes are read-only; callers that need to open or edit
    a file should check this first and reject if True.  Returns False whenever
    the established library root cannot be resolved (degrade-gracefully).

    The resolver is read off the ``graph_adapter`` module at call time (not
    bound by value at import) so tests that monkeypatch
    ``graph_adapter.established_library_root`` — and their teardown that reverts
    it — are observed correctly.
    """
    root = graph_adapter.established_library_root()
    if root is None:
        return False
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or not _FM_FENCE.match(lines[0]):
        return {}
    fm_lines: list[str] = []
    for line in lines[1:]:
        if _FM_FENCE.match(line):
            break
        fm_lines.append(line)
    kv: dict[str, str] = {}
    for line in fm_lines:
        m = re.match(r"^(\w[\w_-]*)\s*:\s*(.+)$", line)
        if m:
            kv[m.group(1).strip()] = m.group(2).strip().strip('"').strip("'")
    return kv


@router.get("/files")
def list_files(settings: Settings = Depends(get_settings)) -> list[dict[str, Any]]:
    """Return metadata for every .md file found under nodes_folders."""
    results: list[dict[str, Any]] = []
    for folder in settings.nodes_folders:
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
                fm = _parse_frontmatter(text)
                results.append(
                    {
                        "path": str(p),
                        "name": p.stem,
                        "filename": p.name,
                        "folder": str(folder),
                        "relative": str(p.relative_to(folder)),
                        "id": fm.get("id", p.stem),
                        "title": fm.get("title", p.stem),
                        "domain": fm.get("domain", ""),
                        "status": fm.get("status", ""),
                        "akms_schema": fm.get("akms_schema", ""),
                        "is_node": fm.get("akms_schema") == "v2",
                    }
                )
            except Exception:
                continue
    return results


def _node_roots(settings: Settings) -> list[Path]:
    """Folders whose files the node routes may read or open.

    The configured node folders, the vault, the AKMS graph vault and the
    shipped established library. Anything else (``/etc/passwd``, a binary
    elsewhere on disk) is refused with a 422 before the filesystem is touched.
    """
    roots: list[Path] = list(settings.nodes_folders)
    if settings.vault_path is not None:
        roots.append(settings.vault_path)
    try:
        roots.append(settings.effective_akms_global_vault())
    except Exception:  # noqa: BLE001 — degrade: just omit this root
        pass
    established = graph_adapter.established_library_root()
    if established is not None:
        roots.append(established)
    return roots


@router.get("/file")
def read_file(
    path: str = Query(...), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    p = resolve_within(path, _node_roots(settings), kind="node file path")
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=415, detail=f"Not a UTF-8 text file: {p.name}"
        ) from None
    fm = _parse_frontmatter(text)
    return {"path": str(p), "content": text, "frontmatter": fm}


class OpenRequest(BaseModel):
    path: str


@router.post("/open")
def open_in_editor(
    req: OpenRequest, settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    """Open the given file in the configured editor or the system default."""
    # Restricted to the node folders: the system opener would otherwise
    # launch any file it is pointed at, executables included.
    p = resolve_within(req.path, _node_roots(settings), kind="node file path")
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.path}")

    # Established (Tier-A) nodes are read-only — reject in-place edits.
    if _is_established_path(p):
        raise HTTPException(
            status_code=403,
            detail=(
                "Established (Tier-A) nodes are read-only and cannot be opened for editing. "
                "Use POST /api/nodes/copy-to-vault to create an editable copy in your vault."
            ),
        )

    if settings.nodes_editor:
        try:
            editor_argv = split_command(settings.nodes_editor)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="nodes_editor setting does not parse as a command"
            ) from None
        cmd = [*editor_argv, str(p)]
        via = "editor"
    elif os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "", str(p)], start_new_session=True)
        return {"status": "opened", "cmd": f"start {p}", "via": "system"}
    else:
        opener = "open" if os.uname().sysname == "Darwin" else "xdg-open"
        if shutil.which(opener) is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"No editor is set and the system opener ({opener}) is not "
                    "installed. Set an editor command under zsum > Settings > "
                    "Nodes Review > Editor command."
                ),
            )
        cmd = [opener, str(p)]
        via = "system"

    try:
        subprocess.Popen(cmd, start_new_session=True)
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Editor command not found: {cmd[0]}. Check zsum > Settings > "
                "Nodes Review > Editor command."
            ),
        ) from None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "opened", "cmd": " ".join(cmd), "via": via}


class CopyToVaultRequest(BaseModel):
    path: str
    """Absolute path to the established node .md file to copy."""


@router.post("/copy-to-vault")
def copy_to_vault(
    req: CopyToVaultRequest, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    """Clone an established (Tier-A) node into the user's workspace as an editable Tier-B node.

    Design decisions:
    - Target: ``<akms_workspace>/generated_nodes/`` — the Logic-Loom-owned writable
      staging tree where user-generated/edited nodes live.  This is the least-surprising
      writable location that is unambiguously the user's own content and is already
      understood by the Nodes Review tab (``nodes_folders``).
    - Non-destructive: never overwrites an existing file.  The copy gets a ``-copy``
      id/filename suffix to avoid id clashes with the established original.
    - Tier-B: the copy has ``status: draft`` (not ``established``) so it is NOT
      treated as an established node by the API or frontend badge logic.
    """
    p = Path(req.path).expanduser().resolve()
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {req.path}")

    if not _is_established_path(p):
        raise HTTPException(
            status_code=400,
            detail="copy-to-vault only accepts established (Tier-A) library nodes.",
        )

    # Resolve the writable target directory.
    target_dir = settings.akms_generated_nodes_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Target directory is not writable: {target_dir}: {exc}",
        )

    # Load the source node.
    try:
        post = frontmatter.load(str(p))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read source node: {exc}")

    # The node id is UNTRUSTED frontmatter (AKMS GlobalNodeFrontmatter
    # only requires `id: str` — no format constraint), so an id containing `/`,
    # `\`, `..`, or an absolute prefix could escape generated_nodes/ or clobber a
    # file outside it. Derive a filename-safe copy_id via a strict allowlist, then
    # re-anchor + verify containment as defence-in-depth against any residue.
    original_id = post.metadata.get("id") or p.stem
    copy_id = _safe_copy_id(original_id)
    if copy_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Node id {original_id!r} is not filename-safe (only "
                "[A-Za-z0-9._-] are allowed); refusing to copy."
            ),
        )
    # Anchor containment on the STABLE workspace root, not on target_dir itself:
    # if generated_nodes were a symlink, `target_dir.resolve()` would follow it
    # to its own target and any child would trivially "contain" — the check must
    # verify the write lands physically under akms_workspace.
    filename = f"{copy_id}.md"
    workspace = settings.akms_workspace.expanduser()
    workspace_root = workspace.resolve()
    target_path = (target_dir / filename).resolve()
    try:
        target_path.relative_to(workspace_root)
        # Parts of the target dir relative to the (unresolved) workspace — used
        # to walk the create down from the pinned root, one openat per level.
        rel_dir_parts = target_dir.relative_to(workspace).parts
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Refusing to write outside the workspace: {target_path}",
        )

    # Build the Tier-B copy: strip established-specific fields, set draft status.
    copy_metadata = dict(post.metadata)
    copy_metadata["id"] = copy_id
    copy_metadata["status"] = "draft"
    # Remove provenance fields that don't belong in a user-editable copy.
    for key in ("is_established", "tier", "verified_by", "verified_at", "version", "source_ref"):
        copy_metadata.pop(key, None)

    copy_post = frontmatter.Post(post.content, **copy_metadata)
    # Hardened exclusive create + write — walks down from the pinned
    # workspace root (one O_NOFOLLOW openat per directory level), keeps the
    # deepest dir fd alive across the write, and cleans up a partial file via that
    # SAME pinned fd. Closes the old exists()→open("wb") TOCTOU/clobber gap AND
    # the parent-directory substitution race (for both the create and the
    # write-failure cleanup) up to the workspace root. See
    # _exclusive_write_under_root.
    try:
        _exclusive_write_under_root(
            workspace_root,
            rel_dir_parts,
            filename,
            # Encode ourselves: python-frontmatter >=1.3 makes dump() write str,
            # which a binary handle rejects.
            lambda f: f.write(frontmatter.dumps(copy_post).encode("utf-8")),
        )
    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A copy already exists at {target_path}. "
                "Delete or rename it before copying again."
            ),
        )
    except _CopyWriteError as exc:
        # write_fn failed mid-stream; the partial file was already unlinked via
        # the pinned dir fd, so the copy stays retryable.
        raise HTTPException(status_code=500, detail=f"Failed to write copy: {exc}")
    except OSError as exc:
        # O_NOFOLLOW on a symlinked directory component surfaces as ELOOP (Linux)
        # or ENOTDIR (macOS: the un-followed symlink is "not a directory"). Both
        # mean a component is a symlink → reject per the physical-containment
        # contract rather than following it.
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise HTTPException(
                status_code=400,
                detail="Refusing to write into a symlinked target directory.",
            )
        raise HTTPException(status_code=500, detail=f"Failed to create copy: {exc}")

    logger.info(
        "copied established node %r → %s (Tier-B draft, id=%r)",
        original_id,
        target_path,
        copy_id,
    )
    return {
        "status": "copied",
        "original_id": original_id,
        "copy_id": copy_id,
        "copy_path": str(target_path),
        "tier": "B",
        "node_status": "draft",
    }


@router.get("/promote/stream")
async def promote_stream(
    source: str = Query(..., description="Absolute path to the staging folder"),
    vault: str | None = Query(default=None, description="Override vault path"),
    promote: bool = Query(default=False, description="Rewrite tentative → established"),
    execute: bool = Query(default=False, description="Actually move files (dry-run if False)"),
    settings: Settings = Depends(get_settings),
):
    """SSE stream of akms_node_promote.py stdout/stderr.

    Runs the promote script from the optional ``akms_repo`` source checkout;
    without one it answers 404 like any other missing script.
    """
    akms_repo = settings.akms_repo_dir()
    if akms_repo is None:
        raise HTTPException(
            status_code=404,
            detail="Promote script not found: no akms_repo source checkout is configured.",
        )
    script = (
        akms_repo
        / "Packages"
        / "AKMS_nodes_gen"
        / "src"
        / "akms_nodes_gen"
        / "akms_node_promote.py"
    )
    if not script.exists():
        raise HTTPException(status_code=404, detail=f"Promote script not found: {script}")

    source_path = Path(source).expanduser().resolve()
    if not source_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Source directory not found: {source}")

    repo_root = akms_repo.resolve()
    try:
        source_rel = str(source_path.relative_to(repo_root))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Source must be inside akms_repo ({repo_root}). Got: {source_path}",
        )

    cmd = ["uv", "run", "python", str(script), "--source", source_rel]
    if vault:
        cmd += ["--vault", vault]
    if promote:
        cmd += ["--promote"]
    if execute:
        cmd += ["--execute"]

    async def event_gen():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            async for raw in proc.stdout:
                yield {"data": raw.decode("utf-8", errors="replace").rstrip()}
            await proc.wait()
            yield {"event": "done", "data": str(proc.returncode)}
        except Exception as exc:
            yield {"event": "error", "data": str(exc)}

    return EventSourceResponse(event_gen())
