#!/usr/bin/env python3
"""Check that every manifest, the changelog and the release tag agree on the version.

Logic-Loom carries its version in seven places across three toolchains. A
release once went out with the Tauri bundle and the Python package still at an
older number than the rest, so bundle file names and `/api/app/health` reported
the wrong version. This check makes that drift an error.

    python scripts/check_versions.py              # manifests agree with each other
    python scripts/check_versions.py --tag v0.2.1 # ... and with the release tag,
                                                  #     and CHANGELOG.md has a section
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_DUNDER_VERSION = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
_LOCK_ENTRY = re.compile(r'\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"')


def _toml_version(rel: str, *keys: str) -> str:
    data = tomllib.loads((ROOT / rel).read_text(encoding="utf-8"))
    for key in keys:
        data = data[key]
    return str(data)


def _json_version(rel: str) -> str:
    return str(json.loads((ROOT / rel).read_text(encoding="utf-8"))["version"])


def _init_version(rel: str) -> str:
    match = _DUNDER_VERSION.search((ROOT / rel).read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"{rel}: no __version__ literal")
    return match.group(1)


def _lock_version(rel: str, package: str) -> str:
    for name, version in _LOCK_ENTRY.findall((ROOT / rel).read_text(encoding="utf-8")):
        if name == package:
            return version
    raise ValueError(f"{rel}: no entry for {package}")


def collect() -> dict[str, str]:
    """Version per location, keyed by a label naming the file."""
    api = "services/logic_loom_api"
    desktop = "apps/desktop"
    return {
        "pyproject.toml": _toml_version("pyproject.toml", "project", "version"),
        f"{api}/pyproject.toml": _toml_version(f"{api}/pyproject.toml", "project", "version"),
        f"{api}/src/logic_loom_api/__init__.py": _init_version(
            f"{api}/src/logic_loom_api/__init__.py"
        ),
        "uv.lock (logic-loom)": _lock_version("uv.lock", "logic-loom"),
        "uv.lock (logic-loom-api)": _lock_version("uv.lock", "logic-loom-api"),
        "package.json": _json_version("package.json"),
        f"{desktop}/package.json": _json_version(f"{desktop}/package.json"),
        f"{desktop}/src-tauri/tauri.conf.json": _json_version(
            f"{desktop}/src-tauri/tauri.conf.json"
        ),
        f"{desktop}/src-tauri/Cargo.toml": _toml_version(
            f"{desktop}/src-tauri/Cargo.toml", "package", "version"
        ),
        f"{desktop}/src-tauri/Cargo.lock": _lock_version(
            f"{desktop}/src-tauri/Cargo.lock", "logic-loom-desktop"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="Expected release tag, for example v0.2.1")
    args = parser.parse_args(argv)

    versions = collect()
    problems: list[str] = []

    distinct = sorted(set(versions.values()))
    if len(distinct) != 1:
        problems.append("version manifests disagree:")
        problems.extend(f"    {label}: {version}" for label, version in versions.items())

    if args.tag:
        expected = args.tag.removeprefix("v")
        for label, version in versions.items():
            if version != expected:
                problems.append(f"{label} is {version}, tag {args.tag} needs {expected}")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        if not re.search(rf"^## v?\[?{re.escape(expected)}\]?\b", changelog, re.MULTILINE):
            problems.append(f"CHANGELOG.md has no section for {expected}")

    if problems:
        print("Version check failed:", file=sys.stderr)
        for problem in dict.fromkeys(problems):
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"Version check passed: {distinct[0]} in {len(versions)} places.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
