"""Read-only vault routes.

  - ``GET /api/vault/tree?root=&depth=`` — lazy directory listing.
  - ``GET /api/vault/file?path=`` — single markdown file with frontmatter.
  - ``GET /api/vault/resolve?wikilink=`` — wikilink → vault path.

All paths are sandboxed to the configured vault root via
``Path.is_relative_to`` checks. Symlinks are resolved before the check, so an
escape-attempt symlink does not leak.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import frontmatter
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from zsum.publisher.resolver import safe_filename

from ..config import Settings
from ..deps import get_settings, get_vault_root
from ..search import SearchHit
from ..search.qmd import QmdBackend
from ..search.ripgrep import RipgrepBackend

router = APIRouter()

EXCLUDED_DIR_NAMES = frozenset({".obsidian", ".git", "node_modules", ".DS_Store", "__pycache__"})
MAX_TREE_DEPTH = 6
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


class TreeNode(BaseModel):
    name: str
    path: str
    kind: str  # "dir" | "file"
    children: list["TreeNode"] | None = None


class FileResponse(BaseModel):
    path: str
    content: str
    frontmatter: dict[str, Any]
    mtime: str
    size: int


class ResolveResponse(BaseModel):
    path: str | None
    exists: bool


def _safe_subpath(vault_root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``vault_root`` and forbid traversal."""
    candidate = (vault_root / rel).resolve()
    root = vault_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal blocked")
    return candidate


def _walk_tree(directory: Path, vault_root: Path, depth: int) -> list[TreeNode]:
    if depth <= 0 or not directory.is_dir():
        return []
    entries: list[TreeNode] = []
    for entry in sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if entry.name in EXCLUDED_DIR_NAMES:
            continue
        rel = entry.relative_to(vault_root).as_posix()
        if entry.is_dir():
            children = _walk_tree(entry, vault_root, depth - 1) if depth > 1 else None
            entries.append(TreeNode(name=entry.name, path=rel, kind="dir", children=children))
        elif entry.is_file():
            entries.append(TreeNode(name=entry.name, path=rel, kind="file"))
    return entries


@router.get("/tree", response_model=list[TreeNode])
def get_tree(
    root: str = Query(default=""),
    depth: int = Query(default=2, ge=1, le=MAX_TREE_DEPTH),
    vault_root: Path = Depends(get_vault_root),
) -> list[TreeNode]:
    target = vault_root if not root else _safe_subpath(vault_root, root)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Not a directory: {root}")
    return _walk_tree(target, vault_root, depth)


@router.get("/file", response_model=FileResponse)
def get_file(
    path: str = Query(...),
    vault_root: Path = Depends(get_vault_root),
) -> FileResponse:
    target = _safe_subpath(vault_root, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Not a file: {path}")
    stat = target.stat()
    if stat.st_size > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large: {stat.st_size} bytes")

    text = target.read_text(encoding="utf-8")
    post = frontmatter.loads(text)
    return FileResponse(
        path=target.relative_to(vault_root).as_posix(),
        content=post.content,
        frontmatter=dict(post.metadata),
        mtime=dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.UTC).isoformat(),
        size=stat.st_size,
    )


@router.get("/resolve", response_model=ResolveResponse)
def resolve(
    wikilink: str = Query(..., min_length=1),
    vault_root: Path = Depends(get_vault_root),
) -> ResolveResponse:
    """Resolve a wikilink to a vault path.

    Order: exact filename match in ``Papers/`` then ``Collections/`` (both
    paper-prefixed ``@`` and bare forms). Falls back to a recursive name
    search across the whole vault.
    """
    name = wikilink.strip()
    candidates = _candidate_names(name)

    for sub in ("Papers", "Collections"):
        sub_path = vault_root / sub
        if not sub_path.is_dir():
            continue
        for cand in candidates:
            target = sub_path / f"{cand}.md"
            if target.is_file():
                return ResolveResponse(
                    path=target.relative_to(vault_root).as_posix(),
                    exists=True,
                )

    # Fallback: recursive search by candidate filename anywhere under root.
    for cand in candidates:
        for hit in vault_root.rglob(f"{cand}.md"):
            if any(part in EXCLUDED_DIR_NAMES for part in hit.relative_to(vault_root).parts):
                continue
            return ResolveResponse(path=hit.relative_to(vault_root).as_posix(), exists=True)

    return ResolveResponse(path=None, exists=False)


@router.get("/collections")
def list_vault_collections(
    vault_root: Path = Depends(get_vault_root),
) -> list[dict]:
    """List collection names by scanning ``<vault>/Collections/*.md``."""
    collections_dir = vault_root / "Collections"
    if not collections_dir.is_dir():
        return []
    names = sorted(
        p.stem for p in collections_dir.iterdir()
        if p.is_file() and p.suffix == ".md" and not p.name.startswith(".")
    )
    return [{"name": n} for n in names]


@router.get("/search", response_model=list[SearchHit])
async def search(
    q: str = Query(..., min_length=1),
    backend: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    vault_root: Path = Depends(get_vault_root),
    settings: Settings = Depends(get_settings),
) -> list[SearchHit]:
    """Search the vault. Backend defaults to ``settings.search_backend``."""
    chosen = backend or settings.search_backend
    if chosen == "ripgrep":
        impl = RipgrepBackend(vault_root)
    elif chosen == "qmd":
        impl = QmdBackend(vault_root, settings.vault_qmd_collection)
    else:
        raise HTTPException(400, detail=f"unsupported search backend: {chosen!r}")
    try:
        return await impl.search(q, limit=limit)
    except RuntimeError as e:
        raise HTTPException(412, detail=str(e))


def _candidate_names(name: str) -> list[str]:
    """Compute the safe filenames to look for, in priority order.

    Paper notes are written as ``@<citekey>.md`` and collections as
    ``<safe_name>.md``. We try both forms when the input doesn't already
    start with ``@``.
    """
    if name.startswith("@"):
        try:
            return [safe_filename(name)]
        except ValueError:
            return []
    out: list[str] = []
    try:
        out.append(safe_filename(f"@{name}"))
    except ValueError:
        pass
    try:
        out.append(safe_filename(name))
    except ValueError:
        pass
    return out
