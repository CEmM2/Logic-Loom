#!/usr/bin/env python3
"""Sync upstream zsum prompts into Logic-Loom's bundled prompt-library defaults.

Reads the prompt templates that ship inside the installed
`zotero-summarizer` package through its public API
(`zsum.resources.iter_builtin_prompts()`, zotero-summarizer >= 1.2) and
mirrors them into
`services/logic_loom_api/src/logic_loom_api/resources/prompt_library/defaults/zsum/<slot>/default.{md,logicloom.json}`.
The script never reads the package's file layout directly.

Two output shapes are supported per the existing bundled-defaults layout:

* `.logicloom.json` — JSON wrapper with structured fields (id, family, slot,
  name, description, source, filename, content_type, content). The script
  parses the existing wrapper, replaces ONLY the ``content`` field, and
  rewrites with stable indentation. All other fields are preserved.
* `.md` — raw content. The upstream text is written verbatim.

Invariants:

* **Whitelist over discovery.** The slot → target-filename map is
  hard-coded. zsum ships more templates than Logic-Loom bundles; adding a
  slot requires editing :data:`SLOT_MAP` plus authoring the matching
  `default.md` (or `default.logicloom.json`).
* **Loud failure on missing source.** A declared slot that the installed
  zsum does not ship is a SystemExit — never silently produce empty bundles.
* **Loud failure on missing target.** The target directory must already
  exist (the slot must be a known canonical slot with a bundled default
  pre-staged). This prevents typos from creating ghost slots.
* **Idempotent.** Re-running with no upstream changes is a no-op.

Usage:

    uv run python scripts/sync_zsum_prompts.py
    pnpm sync:zsum-prompts        # via the npm shortcut

Exit codes:
    0  — all declared slots synced (some may have been no-ops).
    1  — SystemExit: zsum missing or too old, missing source, missing
         target, or unparseable wrapper.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BUNDLED_ROOT = (
    ROOT
    / "services"
    / "logic_loom_api"
    / "src"
    / "logic_loom_api"
    / "resources"
    / "prompt_library"
    / "defaults"
    / "zsum"
)

# zsum slot → target filename relative to <bundled>/<slot>/
# Adding a slot:
#   1) Author a default at BUNDLED_ROOT/<slot>/default.{md,logicloom.json}.
#   2) Append the entry here.
#   3) Ensure the slot id is registered in services/.../prompts/models.py:PromptSlot.
SLOT_MAP: dict[str, str] = {
    "paper_card": "default.logicloom.json",
    "collection_digest": "default.md",
    "collection_digest_structured": "default.md",
    "collection_redigest": "default.md",
    "restructure": "default.md",
}


def _die(msg: str) -> "Never":  # type: ignore[name-defined]
    print(f"[zsum-prompts] ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _sync_logicloom_json(target: Path, upstream_text: str) -> bool:
    """Update only the ``content`` field of a `.logicloom.json` wrapper.

    Returns True when the file changed, False when it was already current.
    All other wrapper fields (id, family, slot, name, description, filename,
    content_type, content_hash, output_schema_id, …) are preserved verbatim
    so user-authored metadata around the prompt body never silently drifts.
    """
    try:
        wrapper = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"cannot parse existing wrapper {target}: {exc}")
    if wrapper.get("content") == upstream_text:
        return False
    wrapper["content"] = upstream_text
    target.write_text(
        json.dumps(wrapper, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True


def _sync_markdown(target: Path, upstream_text: str) -> bool:
    """Write the upstream text verbatim to a `.md` target.

    Returns True when the file changed, False when it was already current.
    Trailing-newline normalization matches what the editor would emit so
    re-running the script the same day doesn't re-touch files.
    """
    desired = upstream_text if upstream_text.endswith("\n") else upstream_text + "\n"
    try:
        current = target.read_text(encoding="utf-8")
    except OSError:
        current = ""
    if current == desired:
        return False
    target.write_text(desired, encoding="utf-8")
    return True


def _builtin_prompts() -> tuple[dict[str, str], str]:
    """Return ``({slot: content}, zsum_version)`` from the installed package."""
    try:
        from zsum.resources import iter_builtin_prompts
    except ImportError:
        _die(
            "zsum.resources is not available. Logic-Loom needs "
            "zotero-summarizer >= 1.2 (a core dependency of logic-loom-api); "
            "run `uv sync` first."
        )
    prompts = list(iter_builtin_prompts())
    version = prompts[0].package_version if prompts else "unknown"
    return {p.slot: p.content for p in prompts}, version


def main() -> int:
    upstream, zsum_version = _builtin_prompts()
    if not BUNDLED_ROOT.is_dir():
        _die(f"bundled defaults root missing: {BUNDLED_ROOT}")

    changed = 0
    noop = 0

    for slot, target_name in SLOT_MAP.items():
        if slot not in upstream:
            _die(
                f"installed zotero-summarizer {zsum_version} ships no prompt "
                f"for slot {slot!r}"
            )
        slot_dir = BUNDLED_ROOT / slot
        if not slot_dir.is_dir():
            _die(
                f"bundled slot dir missing for slot {slot!r}: {slot_dir}. "
                f"Create it and author {target_name} before re-running."
            )
        target = slot_dir / target_name
        if not target.is_file():
            _die(
                f"bundled target missing: {target}. "
                "The sync script only updates existing defaults — author one first."
            )

        upstream_text = upstream[slot]
        if target_name.endswith(".logicloom.json"):
            mutated = _sync_logicloom_json(target, upstream_text)
        else:
            mutated = _sync_markdown(target, upstream_text)

        if mutated:
            changed += 1
            print(f"[zsum-prompts] updated zsum.{slot} <- zsum {zsum_version}")
        else:
            noop += 1
            print(f"[zsum-prompts] up-to-date zsum.{slot}")

    print(
        f"[zsum-prompts] sync complete: {changed} updated, {noop} unchanged, "
        f"{len(SLOT_MAP)} slots total (zotero-summarizer {zsum_version})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
