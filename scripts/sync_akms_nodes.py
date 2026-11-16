#!/usr/bin/env python3
"""Sync curated established nodes into the sidecar resources tree.

Walks ``verified_nodes/`` (repo root), parses each ``.md`` file's YAML
frontmatter, and copies a node into
``services/logic_loom_api/src/logic_loom_api/resources/akms_nodes/established/``
ONLY if ``status == "established"``.

Also carries ``PROVENANCE.toml`` alongside into the shipped tree so the graph/nodes
API can surface provenance/version metadata on its responses.

Invariants:
  * **Filter by status.** Nodes whose frontmatter ``status`` is anything other
    than ``"established"`` (draft, tentative, deprecated, missing) are silently
    skipped. This is the first line of defence; the established-only build guard is
    enforcement.
  * **Exclusions.** ``README.md`` is never treated as a node. ``PROVENANCE.toml``
    is carried alongside (not status-filtered — it is a manifest, not a node).
  * **Loud failure on missing source.** If the source root does not exist we
    raise ``SystemExit``. We would rather break the build than ship silently
    from a missing tree.
  * **Canary guard.** After the copy we assert at least one ``.md`` node landed
    in the output tree. An empty output is treated as a build failure.

Usage (typically invoked from ``scripts/build_sidecar_{macos,linux,windows}.{sh,ps1}``):

    python scripts/sync_akms_nodes.py

Exit codes:
    0   — established nodes copied, canary present.
    1   — ``SystemExit`` (missing source, canary missing, or frontmatter error).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import frontmatter  # python-frontmatter — same lib as graph_adapter.py

ROOT = Path(__file__).resolve().parents[1]

SOURCE_ROOT = ROOT / "verified_nodes"
DEST_ROOT = (
    ROOT
    / "services"
    / "logic_loom_api"
    / "src"
    / "logic_loom_api"
    / "resources"
    / "akms_nodes"
    / "established"
)

PROVENANCE_FILE = "PROVENANCE.toml"
README_FILE = "README.md"
ESTABLISHED_STATUS = "established"


def sync_nodes(source_root: Path, dest_root: Path) -> list[Path]:
    """Copy only ``status: established`` nodes from *source_root* to *dest_root*.

    Args:
        source_root: Root of the curated source tree (e.g. ``verified_nodes/``).
        dest_root:   Destination resources directory.

    Returns:
        List of destination paths actually written (nodes only; PROVENANCE.toml
        is also copied but is not included in the returned list so callers can
        assert on node count cleanly).

    Raises:
        SystemExit: If *source_root* does not exist.

    Ship-safety: *dest_root* is wiped and rebuilt on every call so the shipped
    tree is a faithful mirror of the CURRENT established source. Without this, a
    node later demoted from ``established`` (or renamed/deleted) in the source
    would leave a stale ``status: established`` copy behind — one the established-only
    guard cannot catch (it still reads ``established``) — and it would ship.
    """
    if not source_root.exists():
        raise SystemExit(
            f"[nodes-sync] ERROR: source root does not exist: {source_root}"
        )

    # Clean rebuild: remove ONLY the node output tree (dest_root itself, not its
    # parent), then recreate. This drops any stale/demoted/renamed nodes from a
    # previous sync so no non-current node can linger and ship.
    if dest_root.exists():
        shutil.rmtree(dest_root)
        print(f"[nodes-sync] cleared existing dest tree {dest_root}")
    dest_root.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    skipped = 0

    for md_file in sorted(source_root.rglob("*.md")):
        # Never treat README.md as a node (at any nesting level).
        if md_file.name == README_FILE:
            continue

        rel = md_file.relative_to(source_root)

        # Parse frontmatter to check status.
        try:
            post = frontmatter.load(str(md_file))
        except Exception as exc:
            raise SystemExit(
                f"[nodes-sync] ERROR: failed to parse frontmatter of {md_file}: {exc}"
            ) from exc

        status = post.metadata.get("status", "")
        if status != ESTABLISHED_STATUS:
            print(f"[nodes-sync] skip  {rel}  (status={status!r})")
            skipped += 1
            continue

        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(md_file, dst)
        print(f"[nodes-sync] copy  {rel}")
        copied.append(dst)

    # Carry PROVENANCE.toml alongside (not status-filtered).
    provenance_src = source_root / PROVENANCE_FILE
    if provenance_src.exists():
        provenance_dst = dest_root / PROVENANCE_FILE
        shutil.copy2(provenance_src, provenance_dst)
        print(f"[nodes-sync] copy  {PROVENANCE_FILE}  (provenance, not a node)")
    else:
        print(
            f"[nodes-sync] WARNING: {PROVENANCE_FILE} not found in {source_root}; "
            "skipping provenance copy"
        )

    print(
        f"[nodes-sync] done: {len(copied)} established node(s) copied, "
        f"{skipped} non-established skipped -> {dest_root}"
    )
    return copied


def _assert_canary(dest_root: Path, copied: list[Path]) -> None:
    """Guard: at least one established node must have been synced.

    Raises:
        SystemExit: If no nodes were copied.
    """
    if not copied:
        raise SystemExit(
            f"[nodes-sync] FAIL: no established nodes were copied to {dest_root}. "
            "The source tree may be empty or contain only non-established nodes. "
            "Refusing to proceed with an empty established library."
        )


def main() -> int:
    copied = sync_nodes(SOURCE_ROOT, DEST_ROOT)
    _assert_canary(DEST_ROOT, copied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
