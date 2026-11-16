#!/usr/bin/env python3
"""Build-time security guard: no Nodes_Vault content in shipped resources.

Walks ``services/logic_loom_api/src/logic_loom_api/resources/`` and refuses to
proceed if it finds **any** path component matching ``Nodes_Vault``.

The packaged sidecar ships everything under ``resources/`` inside the wheel
(see ``[tool.hatch.build.targets.wheel.force-include]`` in
``services/logic_loom_api/pyproject.toml``). A user's local node vault leaking
into ``resources/`` would therefore ship verbatim to every tester. This script
is the last line of defence — it MUST run **before** any sidecar / wheel
build step in the three ``scripts/build_sidecar_*`` entry points.

Exit codes:
    * ``0`` — clean. No ``Nodes_Vault`` paths under the resources tree.
    * ``1`` — leak detected. The script prints each offending path and exits
      non-zero so the calling shell script halts via ``set -e`` / ``$ErrorActionPreference = "Stop"``.

Design notes:
    * The check is purely local to this repo; we do NOT look at sibling AKMS
      checkouts. The risk is files that have already been copied into our
      ``resources/`` tree (e.g. via a misconfigured ``sync_akms_claude_assets.py``).
    * The match is on path *components*, not on substring. ``Nodes_Vault_Backup``
      is also rejected (its first ``Nodes_Vault`` component matches), which is
      the conservative behaviour for a security guard.
"""

from __future__ import annotations

import sys
from pathlib import Path


FORBIDDEN_COMPONENT = "Nodes_Vault"


def find_resources_root(repo_root: Path) -> Path:
    return (
        repo_root
        / "services"
        / "logic_loom_api"
        / "src"
        / "logic_loom_api"
        / "resources"
    )


def scan(resources_root: Path) -> list[Path]:
    """Return the list of paths under ``resources_root`` whose path components
    include :data:`FORBIDDEN_COMPONENT`. Empty list = clean.
    """
    leaks: list[Path] = []
    if not resources_root.exists():
        return leaks
    for p in resources_root.rglob("*"):
        # Check directory AND file paths; even an empty leaked directory is
        # a signal that something went wrong in the asset sync step.
        rel_parts = p.relative_to(resources_root).parts
        if FORBIDDEN_COMPONENT in rel_parts or any(
            part.startswith(FORBIDDEN_COMPONENT + "_") for part in rel_parts
        ):
            leaks.append(p)
    return leaks


def main(argv: list[str]) -> int:
    # Repo root is two levels up from this script (scripts/ -> repo).
    repo_root = Path(__file__).resolve().parent.parent
    resources_root = find_resources_root(repo_root)
    leaks = scan(resources_root)
    if leaks:
        print(
            f"[security-guard] FAIL: {len(leaks)} path(s) under "
            f"{resources_root} match the forbidden component "
            f"{FORBIDDEN_COMPONENT!r}:",
            file=sys.stderr,
        )
        for p in leaks[:20]:
            print(f"  - {p}", file=sys.stderr)
        if len(leaks) > 20:
            print(f"  … and {len(leaks) - 20} more", file=sys.stderr)
        print(
            "[security-guard] Refusing to build. Run the asset sync script "
            "(scripts/sync_akms_claude_assets.py) only with a curated source "
            "OR remove the offending paths from resources/.",
            file=sys.stderr,
        )
        return 1
    print(
        f"[security-guard] OK: resources/ has no '{FORBIDDEN_COMPONENT}' paths."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
