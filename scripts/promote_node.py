#!/usr/bin/env python3
"""Promote a reviewed Tier-B draft node into the Tier-A curated library.

Given a human-reviewed draft node (a standard AKMS v2 markdown file), this
script:

  1. Validates the source node against the real AKMS ``GlobalNodeFrontmatter``
     schema (when ``akms`` is importable) so a structurally-broken node can
     never be promoted.
  2. Stamps its frontmatter ``status: established`` (the Tier-A ship gate that
     ``scripts/sync_akms_nodes.py`` filters on and ``scripts/check_resources_
     established_only.py`` enforces).
  3. Writes a matching provenance entry (``verified_by`` / ``verified_at`` /
     ``version`` / ``source_ref``) into ``PROVENANCE.toml`` in the curated tree,
     keyed by the node's ``id``. Provenance CANNOT live in the node frontmatter
     — ``GlobalNodeFrontmatter`` is ``extra="forbid"`` — so it lives in the
     sidecar instead.
  4. Places the node under ``<curated_root>/<domain>/<id>.md``.

The curated SOURCE tree is ``verified_nodes/`` at the repo root — the tree
``scripts/sync_akms_nodes.py`` copies FROM. This is NOT the gitignored
shipped output ``services/logic_loom_api/src/logic_loom_api/resources/akms_nodes/
established/``.

This is distinct from AKMS's own ``akms_node_promote.py`` (the tentative→
established rewriter behind ``GET /api/nodes/promote/stream``). That tool
targets an AKMS vault; this one targets the Logic-Loom curated tree + its
provenance manifest, and does not touch any AKMS vault or checkout.

Human review remains the gate. Promotion assumes the reviewer has worked
through ``verified_nodes/PROMOTION_CHECKLIST.md``. The established-only build
guard (``scripts/check_resources_established_only.py``) is the mechanical
backstop: a promotion that misses required frontmatter/provenance fails the
build.

Usage::

    uv run python scripts/promote_node.py \\
        path/to/draft-node.md \\
        --domain computational-mechanics \\
        --verified-by human-expert \\
        --verified-at 2026-07-01 \\
        --version 1.0 \\
        --source-ref "Timoshenko & Goodier (1951), Theory of Elasticity."

``uv run`` is required — this script imports ``python-frontmatter`` (and, when
available, ``akms``), which are managed dependencies, not on the bare system
interpreter.

Exit codes:
    0   — node promoted; frontmatter stamped, provenance recorded.
    1   — PromotionError (bad source, incomplete provenance, schema failure).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import frontmatter  # python-frontmatter — same lib as sync_akms_nodes.py
import tomli_w
import tomllib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CURATED_ROOT = ROOT / "verified_nodes"

PROVENANCE_FILE = "PROVENANCE.toml"
ESTABLISHED_STATUS = "established"

# Provenance keys required per PROVENANCE.toml entry (mirrors the contract in
# verified_nodes/README.md and PROVENANCE.toml's header).
REQUIRED_PROVENANCE_FIELDS = ("verified_by", "verified_at", "version", "source_ref")


class PromotionError(RuntimeError):
    """Raised when a promotion cannot proceed safely.

    Nothing is written to the curated tree when this is raised — promotion is
    all-or-nothing so a rejected promotion never leaves a partial node behind.
    """


def _validate_akms_schema(metadata: dict) -> None:
    """Validate *metadata* against the real AKMS ``GlobalNodeFrontmatter``.

    Fail-closed on a schema defect. When ``akms`` is not importable (core-only
    env) the check is skipped — the established-only build guard is the
    enforcement layer that fails closed on the release path; this is a
    convenience pre-check.

    Raises:
        PromotionError: If the node fails the schema compile.
    """
    try:
        from akms.schema.validators import GlobalNodeFrontmatter
    except ImportError:
        return  # akms absent — defer to the established-only build guard.

    try:
        GlobalNodeFrontmatter(**metadata)
    except Exception as exc:  # pydantic ValidationError et al.
        raise PromotionError(
            f"node fails the AKMS GlobalNodeFrontmatter schema compile: {exc}"
        ) from exc


def _upsert_provenance(
    curated_root: Path,
    node_id: str,
    *,
    verified_by: str,
    verified_at: str,
    version: str,
    source_ref: str,
) -> None:
    """Insert/replace the ``[<node_id>]`` entry in ``<curated_root>/PROVENANCE.toml``.

    Preserves any existing entries. Creates the file if absent.
    """
    prov_path = curated_root / PROVENANCE_FILE

    data: dict = {}
    if prov_path.exists():
        try:
            data = tomllib.loads(prov_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise PromotionError(
                f"existing {prov_path} is not valid TOML: {exc}"
            ) from exc

    data[node_id] = {
        "verified_by": verified_by,
        "verified_at": verified_at,
        "version": version,
        "source_ref": source_ref,
    }

    curated_root.mkdir(parents=True, exist_ok=True)
    prov_path.write_text(tomli_w.dumps(data), encoding="utf-8")


def promote_node(
    src: Path,
    curated_root: Path,
    *,
    domain: str,
    verified_by: str,
    verified_at: str,
    version: str,
    source_ref: str,
) -> Path:
    """Promote a Tier-B draft node at *src* into the Tier-A *curated_root* tree.

    Stamps ``status: established`` on the node frontmatter, records provenance
    in ``<curated_root>/PROVENANCE.toml`` keyed by the node's ``id``, and writes
    the node to ``<curated_root>/<domain>/<id>.md``.

    Args:
        src:          Path to the reviewed draft node markdown file.
        curated_root: Root of the curated source tree (e.g. ``verified_nodes/``).
        domain:       Domain sub-directory to place the node under.
        verified_by:  Who reviewed/approved the node.
        verified_at:  ISO-8601 date (YYYY-MM-DD) of approval.
        version:      Content/schema version string.
        source_ref:   Free-text citation for the content.

    Returns:
        The destination path the node was written to.

    Raises:
        PromotionError: On any precondition failure — missing/unreadable source,
            missing node ``id``, empty required provenance field, schema compile
            failure, or a destination that already exists (non-destructive).
            When raised, nothing is written to the curated tree.
    """
    # --- Source must exist and be a file ---
    if not src.is_file():
        raise PromotionError(f"source node does not exist or is not a file: {src}")

    # --- Every required provenance field must be non-empty (the human-review
    #     gate is represented mechanically as complete provenance). ---
    provided = {
        "verified_by": verified_by,
        "verified_at": verified_at,
        "version": version,
        "source_ref": source_ref,
    }
    missing = [k for k in REQUIRED_PROVENANCE_FIELDS if not str(provided[k]).strip()]
    if missing:
        raise PromotionError(
            "incomplete promotion — missing required provenance field(s): "
            f"{', '.join(missing)}. Every promoted node needs "
            f"{', '.join(REQUIRED_PROVENANCE_FIELDS)} in PROVENANCE.toml."
        )

    # --- Parse the source frontmatter ---
    try:
        post = frontmatter.load(str(src))
    except Exception as exc:
        raise PromotionError(f"failed to parse frontmatter of {src}: {exc}") from exc

    node_id = post.metadata.get("id")
    if not node_id or not str(node_id).strip():
        raise PromotionError(f"source node {src} has no frontmatter 'id'")

    # --- Guard against provenance keys accidentally living in frontmatter
    #     (GlobalNodeFrontmatter is extra="forbid"; they belong in the sidecar). ---
    forbidden = [k for k in (*REQUIRED_PROVENANCE_FIELDS, "provenance") if k in post.metadata]
    if forbidden:
        raise PromotionError(
            f"source node {src} carries provenance key(s) {forbidden} in its "
            "frontmatter; these must live in PROVENANCE.toml, not the node "
            "(GlobalNodeFrontmatter forbids extra keys)."
        )

    # --- Stamp status: established, then validate the stamped result ---
    post.metadata["status"] = ESTABLISHED_STATUS
    _validate_akms_schema(dict(post.metadata))

    # --- Destination is <curated_root>/<domain>/<id>.md; refuse to clobber ---
    dest_dir = curated_root / domain
    dest = dest_dir / f"{node_id}.md"
    if dest.exists():
        raise PromotionError(
            f"destination already exists: {dest}. Refusing to overwrite an "
            "existing curated node (promotion is non-destructive)."
        )

    # --- Write provenance FIRST, then the node. If either fails the caller sees
    #     a PromotionError; the write order keeps the node from landing without
    #     a provenance entry. ---
    _upsert_provenance(
        curated_root,
        str(node_id),
        verified_by=verified_by,
        verified_at=verified_at,
        version=version,
        source_ref=source_ref,
    )

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")

    return dest


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Promote a reviewed Tier-B draft node into the Tier-A curated "
            "library (verified_nodes/): stamp status: established + record "
            "provenance in PROVENANCE.toml."
        )
    )
    parser.add_argument("src", type=Path, help="Path to the reviewed draft node .md file.")
    parser.add_argument(
        "--domain",
        required=True,
        help="Domain sub-directory to place the node under (e.g. computational-mechanics).",
    )
    parser.add_argument("--verified-by", required=True, help="Who reviewed/approved the node.")
    parser.add_argument(
        "--verified-at", required=True, help="ISO-8601 date (YYYY-MM-DD) of approval."
    )
    parser.add_argument("--version", required=True, help="Content/schema version string.")
    parser.add_argument(
        "--source-ref", required=True, help="Free-text citation for the content."
    )
    parser.add_argument(
        "--curated-root",
        type=Path,
        default=DEFAULT_CURATED_ROOT,
        help=f"Curated source tree root (default: {DEFAULT_CURATED_ROOT}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        dest = promote_node(
            args.src,
            args.curated_root,
            domain=args.domain,
            verified_by=args.verified_by,
            verified_at=args.verified_at,
            version=args.version,
            source_ref=args.source_ref,
        )
    except PromotionError as exc:
        print(f"[promote-node] FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        f"[promote-node] OK: promoted -> {dest}  "
        f"(status: established, provenance recorded in {PROVENANCE_FILE})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
