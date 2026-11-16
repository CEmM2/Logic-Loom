#!/usr/bin/env python3
"""Build-time established-only guard.

Walks ``services/logic_loom_api/src/logic_loom_api/resources/akms_nodes/established/``
and refuses to proceed if any ``.md`` file (excluding ``README.md``) either:

  1. Carries a ``status`` value that is NOT ``"established"`` in its YAML
     frontmatter (including missing or malformed status — both are offenders,
     not crashes); OR
  2. Fails the real AKMS ``GlobalNodeFrontmatter`` schema compile
     (pydantic validation) — meaning the node has a structural defect beyond
     just the status field.

Defence-in-depth design:
  The ``sync_akms_nodes.py`` sync is the first line of defence (it skips non-established nodes
  at copy time).  This guard is the enforcement layer — a mechanical build
  gate that makes shipping any non-established or structurally-invalid node
  structurally impossible.

  The AKMS compile check is REQUIRED by default (fail-closed): when ``akms``
  is importable (dev env / ``uv run`` with ``uv sync --extra akms``) the guard
  exercises the full schema compile.  When ``akms`` is absent the guard REFUSES
  to certify (exit 1) — a build/ship gate is a security boundary and must not
  silently downgrade to a weaker check (P4F-3).  A ``--allow-missing-akms``
  (alias ``--dev``) opt-out falls back to the status-only check for
  local/core-only runs at the caller's own risk.

Exit codes:
    * ``0`` — clean. All nodes under the established tree carry
      ``status: established`` and (with akms importable, or ``--allow-missing-akms``
      + status-only) pass the checks.  Prints ``[established-guard] OK``.
    * ``1`` — offender(s) detected, OR ``akms`` is not importable and
      ``--allow-missing-akms`` was NOT passed (fail-closed). The reason is
      printed to stderr; the calling shell script halts via ``set -e`` /
      ``$ErrorActionPreference = "Stop"``.

Usage in build scripts (mirror of check_resources_no_vault.py pattern):

    uv run python scripts/check_resources_established_only.py

``uv run`` is required because ``python-frontmatter`` (and optionally
``akms``) are managed dependencies, not available on the bare system
interpreter on a clean build host.
"""

from __future__ import annotations

import sys
from pathlib import Path

import frontmatter  # python-frontmatter — always available in the uv env


# Optional AKMS schema compile — graceful degradation when akms is absent.
try:
    from akms.schema.validators import GlobalNodeFrontmatter as _GlobalNodeFrontmatter

    _AKMS_AVAILABLE = True
except ImportError:
    _GlobalNodeFrontmatter = None  # type: ignore[assignment,misc]
    _AKMS_AVAILABLE = False

REQUIRED_STATUS = "established"


def _established_root_default(repo_root: Path) -> Path:
    return (
        repo_root
        / "services"
        / "logic_loom_api"
        / "src"
        / "logic_loom_api"
        / "resources"
        / "akms_nodes"
        / "established"
    )


def scan(established_root: Path) -> list[Path]:
    """Return the list of ``.md`` paths under ``established_root`` that are
    offenders — either lacking ``status: established`` or (when akms is
    importable) failing the ``GlobalNodeFrontmatter`` schema compile.

    ``README.md`` (any casing) is excluded.  ``PROVENANCE.toml`` and other
    non-``.md`` files are excluded naturally (not matched by the glob).

    Returns an empty list when the tree is clean or does not exist.
    """
    offenders: list[Path] = []
    if not established_root.exists():
        return offenders

    for path in sorted(established_root.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue

        # --- Status check (always runs) ---
        try:
            post = frontmatter.load(str(path))
            status = post.get("status")
        except Exception:
            # Malformed YAML / unreadable file → offender, not a crash.
            offenders.append(path)
            continue

        if status != REQUIRED_STATUS:
            offenders.append(path)
            continue

        # --- AKMS schema compile check (opportunistic) ---
        if _AKMS_AVAILABLE:
            try:
                _GlobalNodeFrontmatter(**post.metadata)
            except Exception:
                offenders.append(path)

    return offenders


_ALLOW_MISSING_AKMS_FLAGS = ("--allow-missing-akms", "--dev")


def main(argv: list[str]) -> int:
    # Repo root is two levels up from this script (scripts/ -> repo).
    repo_root = Path(__file__).resolve().parent.parent
    established_root = _established_root_default(repo_root)

    # P4F-3 (F3): FAIL CLOSED on a release/build path. This guard advertises
    # "mechanically impossible to ship a bad node" — a guarantee that only holds
    # when the full GlobalNodeFrontmatter schema compile can run. If ``akms`` is
    # NOT importable we cannot run it, so by default we REFUSE to certify (exit
    # non-zero) rather than silently degrade to the status-only check. The three
    # sidecar build scripts invoke this via ``uv run`` (akms present), so this
    # never blocks a real build; it only bites a mis-provisioned env that would
    # otherwise fail open. A ``--allow-missing-akms`` / ``--dev`` opt-out keeps
    # core-only / local runs unblocked (status-only, at the caller's own risk).
    #
    # NB: this is deliberately STRICTER than the usual "degrade gracefully if
    # akms absent" posture. That graceful-degrade rule is correct for the
    # *runtime* graph compile (a render path); a *build/ship gate* is a
    # security boundary and must fail closed.
    allow_missing_akms = any(flag in argv for flag in _ALLOW_MISSING_AKMS_FLAGS)
    if not _AKMS_AVAILABLE and not allow_missing_akms:
        print(
            "[established-guard] FAIL: the AKMS schema validator is not importable, "
            "so the GlobalNodeFrontmatter compile check cannot run. Refusing to "
            "certify the established library (fail-closed). Install the akms extra "
            "(uv sync --extra akms) or, for a local/core-only run only, pass "
            "--allow-missing-akms to fall back to the status-only check.",
            file=sys.stderr,
        )
        return 1

    offenders = scan(established_root)
    if offenders:
        akms_note = " + akms schema compile" if _AKMS_AVAILABLE else " (status-only; akms not installed)"
        print(
            f"[established-guard] FAIL: {len(offenders)} offending path(s) under "
            f"{established_root}{akms_note}:",
            file=sys.stderr,
        )
        for p in offenders[:20]:
            print(f"  - {p}", file=sys.stderr)
        if len(offenders) > 20:
            print(f"  … and {len(offenders) - 20} more", file=sys.stderr)
        print(
            "[established-guard] Refusing to build. Every .md file under "
            "resources/akms_nodes/established/ must carry status: established "
            "and pass the AKMS GlobalNodeFrontmatter schema compile. "
            "Run scripts/sync_akms_nodes.py to regenerate from verified_nodes/.",
            file=sys.stderr,
        )
        return 1

    akms_note = " + akms schema compile" if _AKMS_AVAILABLE else " (status-only; akms not installed)"
    print(
        f"[established-guard] OK: all nodes in {established_root} "
        f"pass the established-only check{akms_note}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
