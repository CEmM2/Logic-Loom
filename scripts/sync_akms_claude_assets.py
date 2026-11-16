#!/usr/bin/env python3
"""Sync curated AKMS Claude assets into the sidecar resources tree.

Reads `packaging/akms_claude_assets.toml` (the curated whitelist) and copies
every declared file into
`services/logic_loom_api/src/logic_loom_api/resources/akms_claude/`, where
the wheel's `force-include` mapping picks them up.

A section's `source` is either a path relative to the repository root, or
`pkg:<import name>/<path inside the package>` for files that ship inside an
installed distribution. The AKMS agent assets (skills, agents, commands,
hooks) are read from the installed `akms` wheel's `_bundled/` tree, so what
Logic-Loom bundles always matches the AKMS version it is built against. That
needs the `akms` extra: `uv sync --package logic-loom-api --extra akms`.

Invariants:
  * **No globs.** The manifest is the single source of truth — every file is
    listed explicitly. This is a security boundary: Nodes_Vault/, generated
    nodes, credentials, and session state must never ship in the binary.
  * **Loud failure on missing source.** If any declared file is absent at
    sync time we raise `SystemExit`. We would rather break the build than
    ship a half-stripped skill.
  * **Canary guard.** After all sections are processed we assert at least
    one `SKILL.md` exists under `.claude/skills/node-gen/` in the bundled
    resources tree. If the tree lacks this canary, raise.

Usage (the sidecar build scripts run it with the build environment's
interpreter, after the AKMS packages are installed there):

    uv run --package logic-loom-api --extra akms python scripts/sync_akms_claude_assets.py

Exit codes:
    0   — all declared files copied, canary present.
    1   — `SystemExit` (missing source, canary missing, or manifest broken).
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packaging" / "akms_claude_assets.toml"

# Section groups we iterate, in stable order. Mirrors the manifest spec.
# `prompt_library_defaults` sources live in-tree
# under `resources/prompt_library/defaults/` (authored directly, not
# taken from an AKMS package), so the copy is an identity self-copy.
# Listing them here documents the bundled surface for the wheel and lets
# packaging validate that every declared default is present at sync time.
SECTION_GROUPS: tuple[str, ...] = (
    "skills",
    "agents",
    "commands",
    "root_scripts",
    # `hooks` come from the installed `akms` wheel like the skills; `docs` is
    # the conformance-invariants page, which no wheel ships, so its source is
    # the in-tree copy under `packaging/akms_assets/`.
    "hooks",
    "docs",
    "prompt_library_defaults",
)

# Canary path (relative to a section's target_root once it lands on disk).
# At least one such file must exist anywhere in the materialized tree
# rooted at services/logic_loom_api/src/logic_loom_api/resources/akms_claude.
RESOURCES_ROOT = (
    ROOT / "services" / "logic_loom_api" / "src" / "logic_loom_api" / "resources"
    / "akms_claude"
)
CANARY_REL = Path(".claude") / "skills" / "node-gen" / "SKILL.md"


PACKAGE_SOURCE_PREFIX = "pkg:"


def resolve_source_root(source: str, section_name: str = "") -> Path:
    """Resolve a manifest `source` to a directory.

    `pkg:<import name>/<subpath>` resolves inside an installed package, located
    with `find_spec` so the package is never imported. Anything else is a path
    relative to the repository root. Raises `SystemExit` when the package is not
    installed: the build must stop rather than ship without the assets.
    """
    if not source.startswith(PACKAGE_SOURCE_PREFIX):
        return (ROOT / source).resolve()

    package, _, subpath = source[len(PACKAGE_SOURCE_PREFIX):].partition("/")
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        spec = None
    locations = list(spec.submodule_search_locations or []) if spec else []
    if not locations:
        raise SystemExit(
            f"Package {package!r} is not installed, so the source for "
            f"{section_name or source} cannot be read. Install the AKMS extra "
            "first: uv sync --package logic-loom-api --extra akms"
        )
    return (Path(locations[0]) / subpath).resolve()


def _apply_replacements(text: str, replacements: list[dict]) -> str:
    """Apply a section's declared `replace` rules (regular expressions)."""
    for rule in replacements:
        text = re.sub(rule["find"], rule["replace"], text)
    return text


def copy_declared_files(section: dict, section_name: str) -> int:
    """Copy every file declared under `section`.

    Returns the number of files copied. Raises `SystemExit` if the source
    directory or any declared source file is missing.

    A section may declare `replace = [{ find = "...", replace = "..." }]`:
    regular-expression rewrites applied to each copied text file. It exists for
    assets whose text names their own install path, which differs between the
    AKMS wheel layout and the layout Logic-Loom materialises.
    """
    source_root = resolve_source_root(section["source"], section_name)
    target_root = (ROOT / section["target"]).resolve()
    replacements = section.get("replace") or []

    if not source_root.exists():
        raise SystemExit(
            f"Missing source directory for {section_name}: {source_root}"
        )

    target_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    for rel in section["files"]:
        src = source_root / rel
        dst = target_root / rel
        if not src.exists():
            raise SystemExit(f"Missing declared AKMS Claude asset: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        # prompt_library_defaults sections have source == target
        # (identity self-copy). shutil.copy2 raises SameFileError in that
        # case; treat as a no-op so the validation pass still confirms the
        # file is present.
        if src.resolve() == dst.resolve():
            print(f"[akms-assets] verified in-place {src}")
        elif replacements:
            text = src.read_text(encoding="utf-8")
            dst.write_text(_apply_replacements(text, replacements), encoding="utf-8")
            shutil.copymode(src, dst)
            print(f"[akms-assets] copied (rewritten) {src} -> {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"[akms-assets] copied {src} -> {dst}")
        copied += 1
    return copied


def _assert_canary(resources_root: Path) -> None:
    """Guard: the materialized tree must contain at least one SKILL.md
    under `.claude/skills/node-gen/`. Otherwise we have a half-stripped skill.
    """
    canary = resources_root / CANARY_REL
    if not canary.exists():
        raise SystemExit(
            f"AKMS Claude asset canary missing: expected {canary} after sync. "
            "The bundled skill tree is incomplete — refusing to proceed."
        )


def main() -> int:
    if not MANIFEST.exists():
        raise SystemExit(f"Manifest not found: {MANIFEST}")

    data = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))

    total_copied = 0
    total_sections = 0
    for group_name in SECTION_GROUPS:
        for name, section in data.get(group_name, {}).items():
            total_copied += copy_declared_files(section, f"{group_name}.{name}")
            total_sections += 1

    _assert_canary(RESOURCES_ROOT)

    print(
        f"[akms-assets] sync complete: {total_copied} files across "
        f"{total_sections} sections -> {RESOURCES_ROOT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
