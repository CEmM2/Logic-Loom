#!/usr/bin/env python3
"""Reject private-development residue, obvious secrets and local paths.

Two modes:

* default: the full audit, for the tree that is published. It fails on a
  private working folder, a private repository reference, a real home-directory
  path or a secret anywhere in the tree.
* ``--security-only``: secrets and real home-directory paths only, skipping the
  private working folders. This is the mode for a development checkout, which
  legitimately carries those folders.

``--root`` audits another directory (the publishing gate points it at the
sanitized tree).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# Top-level paths that belong to private development and never to the published
# tree. `thirdparty` held snapshots of other repositories, including knowledge
# content that is not redistributable; it was deleted once everything came from
# PyPI, and stays listed so a stray copy can never be published.
FORBIDDEN_TOP_LEVEL = {
    ".agents",
    ".claude",
    ".codex",
    ".orchestra",
    ".comment-review",
    "AGENTS.md",
    "CLAUDE.md",
    "artifacts",
    "dev",
    "thirdparty",
}

FORBIDDEN_NAME_FRAGMENTS = (
    "Handoff_Phase",
    "SUPERSEDED",
    "session-state",
    "session-log",
    "tasks-tracker",
)

# Repositories under the personal account that are deliberately public and may
# be referenced. Everything else under that account is assumed private.
PUBLIC_PERSONAL_REPOS = ("Teaching-materials",)

REFERENCE_PATTERNS = {
    # The trailing `(?![\w.-])` keeps the allowlist exact: without it
    # `Teaching-materials-private` would pass on its allowlisted prefix.
    "private repository reference": re.compile(
        r"\bSOSOVSKI/(?!(?:" + "|".join(PUBLIC_PERSONAL_REPOS) + r")(?![\w.-]))[\w.-]+",
        re.I,
    ),
    "private project board reference": re.compile(r"users/SOSOVSKI/projects", re.I),
}

# Home-directory names that are documentation placeholders, not a real account.
PLACEHOLDER_USERS = {
    "<user>",
    "<you>",
    "<username>",
    "&lt;you&gt;",
    "alice",
    "bob",
    "me",
    "name",
    "runner",
    "someone",
    "tester",
    "u",
    "user",
    "username",
    "you",
    "your-name",
    "yourname",
}

PATH_PATTERNS = {
    "absolute macOS path": re.compile(r"/Users/(?P<user>[^/\s\"'`]+)/"),
    "absolute Linux home path": re.compile(r"(?<![\w.])/home/(?P<user>[^/\s\"'`]+)/"),
    "absolute Windows user path": re.compile(
        r"[A-Za-z]:\\{1,2}Users\\{1,2}(?P<user>[^\\\s\"'`]+)\\"
    ),
}

SECRET_PATTERNS = {
    "GitHub classic token": re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    "GitHub fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}

SKIP_DIRS = {
    ".git",
    ".venv",
    ".venv-sidecar-build",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "target",
    "dist",
}

BINARY_SUFFIXES = {
    ".7z", ".bz2", ".dmg", ".docx", ".gif", ".gz", ".icns", ".ico", ".jar",
    ".jpeg", ".jpg", ".lock", ".pdf", ".png", ".pyc", ".so", ".sqlite", ".tar",
    ".tgz", ".ttf", ".webp", ".whl", ".woff", ".woff2", ".xlsx", ".zip",
}  # fmt: skip

# This script names the patterns it searches for.
SELF = "scripts/check_public_tree.py"


def publishable_paths(root: Path) -> set[Path] | None:
    """Every path git would publish: tracked, plus untracked-but-not-ignored.

    Returns None when git cannot answer (no repository, an extracted archive),
    so the caller falls back to walking the tree, the conservative direction.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others",
             "--exclude-standard", "-z"],  # fmt: skip
            capture_output=True,
            check=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    names = result.stdout.decode("utf-8", "replace").split("\0")
    return {root / name for name in names if name}


def iter_files(root: Path, publishable: set[Path] | None) -> list[Path]:
    if publishable is not None:
        candidates = [path for path in publishable if path.is_file()]
    else:
        candidates = [path for path in root.rglob("*") if path.is_file()]
    return sorted(
        path
        for path in candidates
        if not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
    )


def scan_text(root: Path, path: Path, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    findings: list[str] = []
    for label, pattern in patterns.items():
        for match in pattern.finditer(text):
            user = match.groupdict().get("user")
            if user is not None and user.lower() in PLACEHOLDER_USERS:
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{path.relative_to(root).as_posix()}:{line}: {label}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--security-only",
        action="store_true",
        help="Scan only for secrets and real home-directory paths, outside the private working folders.",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Tree to audit.")
    args = parser.parse_args()
    root = args.root.resolve()

    findings: list[str] = []
    publishable = publishable_paths(root)
    files = iter_files(root, publishable)

    if not args.security_only:
        present = {path.relative_to(root).parts[0] for path in files}
        for name in sorted(FORBIDDEN_TOP_LEVEL & present):
            findings.append(f"{name}: forbidden top-level public path")
        for path in files:
            if path.relative_to(root).parts[0] in FORBIDDEN_TOP_LEVEL:
                continue
            if any(fragment.lower() in path.name.lower() for fragment in FORBIDDEN_NAME_FRAGMENTS):
                findings.append(f"{path.relative_to(root).as_posix()}: private-development filename")

    patterns = dict(SECRET_PATTERNS)
    patterns.update(PATH_PATTERNS)
    if not args.security_only:
        patterns.update(REFERENCE_PATTERNS)

    for path in files:
        rel = path.relative_to(root)
        if rel.parts[0] in FORBIDDEN_TOP_LEVEL:
            continue  # already reported as a whole in the full audit; private otherwise
        if rel.as_posix() == SELF or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        findings.extend(scan_text(root, path, patterns))

    if findings:
        print("Public-tree audit failed:", file=sys.stderr)
        for finding in sorted(set(findings)):
            print(f"  - {finding}", file=sys.stderr)
        return 1

    print("Public-tree audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
