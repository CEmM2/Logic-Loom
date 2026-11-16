#!/usr/bin/env python3
"""Sync upstream zsum prompts into Logic-Loom's bundled prompt-library defaults.

Mirrors the curated `.txt` prompts that ship inside the installed
`zotero-summarizer` package (`zsum/prompts/<slot>.txt`) into
`services/logic_loom_api/src/logic_loom_api/resources/prompt_library/defaults/zsum/<slot>/default.{md,logicloom.json}`.

Two output shapes are supported per the existing bundled-defaults layout:

* `.logicloom.json` — JSON wrapper with structured fields (id, family, slot,
  name, description, source, filename, content_type, content). The script
  parses the existing wrapper, replaces ONLY the ``content`` field, and
  rewrites with stable indentation. All other fields are preserved.
* `.md` — raw content. The upstream text is written verbatim.

Invariants:

* **Whitelist over glob.** The slot → upstream-filename map is hard-coded.
  Adding a new zsum slot requires editing :data:`SLOT_MAP` plus authoring
  the matching `default.md` (or `default.logicloom.json`).
* **Loud failure on missing source.** A declared upstream file that does
  not exist is a SystemExit — never silently produce empty bundles.
* **Loud failure on missing target.** The target directory must already
  exist (the slot must be a known canonical slot with a bundled default
  pre-staged). This prevents typos from creating ghost slots.
* **Idempotent.** Re-running with no upstream changes is a no-op.

Usage:

    uv run python scripts/sync_zsum_prompts.py
    pnpm sync:zsum-prompts        # via the npm shortcut

Exit codes:
    0  — all declared slots synced (some may have been no-ops).
    1  — SystemExit: missing source, missing target, or unparseable wrapper.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _installed_zsum_prompts() -> Path | None:
    """The `prompts/` directory of the installed `zsum` package, if any.

    Located with `find_spec`, so the package is not imported. zotero-summarizer
    is a core dependency of logic-loom-api, so any synced environment has it.
    """
    try:
        spec = importlib.util.find_spec("zsum")
    except (ImportError, ValueError):
        spec = None
    locations = list(spec.submodule_search_locations or []) if spec else []
    return Path(locations[0]) / "prompts" if locations else None


UPSTREAM_PROMPTS = _installed_zsum_prompts()
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

# slot_stem → (upstream-filename, target-filename relative to <bundled>/<slot_stem>/)
# Adding a slot:
#   1) Author a default at BUNDLED_ROOT/<stem>/default.{md,logicloom.json}.
#   2) Append the entry here.
#   3) Ensure the slot id is registered in services/.../prompts/models.py:PromptSlot.
SLOT_MAP: dict[str, tuple[str, str]] = {
    "paper_card": ("paper_card.txt", "default.logicloom.json"),
    "collection_digest": ("collection_digest.txt", "default.md"),
    "collection_digest_structured": (
        "collection_digest_structured.txt",
        "default.md",
    ),
    "collection_redigest": ("collection_redigest.txt", "default.md"),
    "restructure": ("restructure.txt", "default.md"),
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


def main() -> int:
    if UPSTREAM_PROMPTS is None or not UPSTREAM_PROMPTS.is_dir():
        _die(
            "the installed zotero-summarizer package has no prompts directory "
            f"({UPSTREAM_PROMPTS}). Run `uv sync` first; zotero-summarizer is a "
            "core dependency of logic-loom-api."
        )
    if not BUNDLED_ROOT.is_dir():
        _die(f"bundled defaults root missing: {BUNDLED_ROOT}")

    changed = 0
    noop = 0

    for stem, (upstream_name, target_name) in SLOT_MAP.items():
        src = UPSTREAM_PROMPTS / upstream_name
        if not src.is_file():
            _die(f"upstream prompt missing for slot {stem!r}: {src}")
        slot_dir = BUNDLED_ROOT / stem
        if not slot_dir.is_dir():
            _die(
                f"bundled slot dir missing for slot {stem!r}: {slot_dir}. "
                f"Create it and author {target_name} before re-running."
            )
        target = slot_dir / target_name
        if not target.is_file():
            _die(
                f"bundled target missing: {target}. "
                "The sync script only updates existing defaults — author one first."
            )

        upstream_text = src.read_text(encoding="utf-8")
        if target_name.endswith(".logicloom.json"):
            mutated = _sync_logicloom_json(target, upstream_text)
        else:
            mutated = _sync_markdown(target, upstream_text)

        if mutated:
            changed += 1
            print(f"[zsum-prompts] updated zsum.{stem} <- zsum/prompts/{upstream_name}")
        else:
            noop += 1
            print(f"[zsum-prompts] up-to-date zsum.{stem}")

    print(
        f"[zsum-prompts] sync complete: {changed} updated, {noop} unchanged, "
        f"{len(SLOT_MAP)} slots total"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
