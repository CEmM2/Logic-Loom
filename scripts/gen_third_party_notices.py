#!/usr/bin/env python3
"""Generate THIRD_PARTY_NOTICES.md: every third-party package the desktop app ships.

The app bundles three dependency sets, and this lists all of them:

* Python: the frozen sidecar. The same locked set the sidecar build installs
  (`uv export --locked --no-dev --package logic-loom-api` with the sidecar
  extras), downloaded for each build platform into a temporary directory.
* JavaScript: the production dependencies of `apps/desktop`, which Vite bundles
  into the web view (`pnpm licenses list --prod`).
* Rust: the normal (non-build, non-dev) dependency graph of the Tauri shell,
  for every target platform (`cargo metadata`).

For each package the file gives name, version, declared license and source URL,
followed by the license and notice files the package itself ships, with
identical texts printed once.

    uv run python scripts/gen_third_party_notices.py          # regenerate
    python3 scripts/gen_third_party_notices.py --check        # is it current?

Regenerating needs network access (it downloads the Python set for Linux, macOS and Windows, and lets cargo
fetch crate sources), `uv`, `pnpm` with `node_modules` installed, and `cargo`.
`--check` needs none of that: the file records the SHA-256 of the three
lockfiles it was generated from, and the check compares them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "THIRD_PARTY_NOTICES.md"
TAURI_DIR = ROOT / "apps" / "desktop" / "src-tauri"
DESKTOP_PKG = "logic-loom-desktop"
# Must match SIDECAR_EXTRAS in scripts/build_sidecar_*.{sh,ps1}.
SIDECAR_EXTRAS = ("akms", "mechdsl", "failure-memory", "records")
# The platforms the release workflow builds the sidecar on.
PY_PLATFORMS = ("x86_64-manylinux_2_28", "aarch64-apple-darwin", "x86_64-pc-windows-msvc")
LOCKFILES = {
    "uv.lock": ROOT / "uv.lock",
    "pnpm-lock.yaml": ROOT / "pnpm-lock.yaml",
    "Cargo.lock": TAURI_DIR / "Cargo.lock",
}
# Our own packages: covered by the repository LICENSE, not third-party.
FIRST_PARTY = {"logic-loom-api", "logic-loom", DESKTOP_PKG, "logic_loom"}
LICENSE_FILE_RE = re.compile(r"^(licen[cs]e|copying|notice|copyright|authors)([-._].*)?$", re.IGNORECASE)
HASH_LINE_RE = re.compile(r"<!-- lockfile-sha256: (.*?) -->")

# Read inside the throwaway venv: one record per installed distribution.
PY_METADATA_SCRIPT = r"""
import json, sys, importlib.metadata as md
out = []
for d in md.distributions(path=sys.argv[1:]):
    m = d.metadata
    urls = {}
    for entry in m.get_all("Project-URL") or []:
        k, _, v = entry.partition(",")
        urls[k.strip().lower()] = v.strip()
    texts = []
    for rel in m.get_all("License-File") or []:
        for cand in (f"licenses/{rel}", rel):
            p = d.locate_file(f"{d._path.name}/{cand}") if hasattr(d, "_path") else None
            if p is not None and p.is_file():
                texts.append((rel, p.read_text(encoding="utf-8", errors="replace")))
                break
    if not texts and hasattr(d, "_path"):
        for p in sorted(d._path.rglob("*")):
            n = p.name.lower()
            if p.is_file() and (n.startswith("licen") or n.startswith("copying") or n.startswith("notice")):
                texts.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
    out.append({
        "name": m["Name"], "version": m["Version"],
        "license_expression": m.get("License-Expression"),
        "license": m.get("License"),
        "classifiers": [c for c in (m.get_all("Classifier") or []) if c.startswith("License ::")],
        "homepage": m.get("Home-page") or urls.get("source") or urls.get("repository")
                    or urls.get("homepage") or urls.get("source code") or next(iter(urls.values()), None),
        "texts": texts,
    })
print(json.dumps(out))
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lock_hashes() -> str:
    return ", ".join(f"{name}={sha256(p)[:16]}" for name, p in LOCKFILES.items())


def norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def short_license(text: str | None) -> str | None:
    """A free-text License field is sometimes the whole license; keep a label only."""
    if not text:
        return None
    text = text.strip()
    if "\n" in text or len(text) > 80:
        # Some packages paste the whole license here; its first line is the name.
        first = text.splitlines()[0].strip()
        return first if 0 < len(first) <= 40 and "licen" in first.lower() else None
    return text


def classifier_license(classifiers: list[str]) -> str | None:
    names = [c.split("::")[-1].strip() for c in classifiers]
    return " OR ".join(names) if names else None


def python_packages() -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="ll-notices-") as tmp:
        req = Path(tmp) / "requirements.txt"
        cmd = ["uv", "export", "--locked", "--no-hashes", "--no-dev", "--no-emit-workspace",
               "--package", "logic-loom-api", "--output-file", str(req)]
        for extra in SIDECAR_EXTRAS:
            cmd += ["--extra", extra]
        subprocess.run(cmd, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        # The locked set differs per platform (colorama is Windows-only, for
        # example), so install it once per target the sidecar is built for and
        # list the union. `--target` installs never execute package code.
        targets = []
        for platform in PY_PLATFORMS:
            target = Path(tmp) / platform
            subprocess.run(["uv", "pip", "install", "-q", "--target", str(target),
                            "--python-version", "3.12", "--python-platform", platform,
                            "-r", str(req)], check=True)
            targets.append(str(target))
        res = subprocess.run([sys.executable, "-I", "-c", PY_METADATA_SCRIPT, *targets], check=True,
                             capture_output=True, text=True)
    pkgs, seen = [], set()
    for d in json.loads(res.stdout):
        key = (norm_name(d["name"]), d["version"])
        if key[0] in FIRST_PARTY or key in seen:
            continue
        seen.add(key)
        lic = (d["license_expression"] or short_license(d["license"])
               or classifier_license(d["classifiers"]) or "UNKNOWN")
        pkgs.append({"name": d["name"], "version": d["version"], "license": lic,
                     "url": d["homepage"] or f"https://pypi.org/project/{d['name']}/",
                     "texts": d["texts"]})
    return pkgs


def license_files(directory: Path) -> list[tuple[str, str]]:
    if not directory.is_dir():
        return []
    return [(p.name, p.read_text(encoding="utf-8", errors="replace"))
            for p in sorted(directory.iterdir()) if p.is_file() and LICENSE_FILE_RE.match(p.name)]


def js_packages() -> list[dict]:
    res = subprocess.run(["pnpm", "--filter", DESKTOP_PKG, "licenses", "list", "--prod", "--json"],
                         cwd=ROOT, check=True, capture_output=True, text=True)
    pkgs = []
    for lic, entries in json.loads(res.stdout).items():
        for e in entries:
            if e["name"] in FIRST_PARTY:
                continue
            for version, path in zip(e["versions"], e["paths"]):
                pkgs.append({"name": e["name"], "version": version, "license": lic,
                             "url": e.get("homepage") or f"https://www.npmjs.com/package/{e['name']}",
                             "texts": license_files(Path(path))})
    return pkgs


def rust_packages() -> list[dict]:
    res = subprocess.run(["cargo", "metadata", "--format-version", "1", "--locked"],
                         cwd=TAURI_DIR, check=True, capture_output=True, text=True)
    meta = json.loads(res.stdout)
    by_id = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    roots = list(meta["workspace_members"])
    # Walk normal dependencies only: build scripts and dev-deps are not shipped.
    seen, stack = set(roots), list(roots)
    while stack:
        for dep in nodes[stack.pop()]["deps"]:
            if any(k["kind"] is None for k in dep["dep_kinds"]) and dep["pkg"] not in seen:
                seen.add(dep["pkg"])
                stack.append(dep["pkg"])
    pkgs = []
    for pid in seen:
        if pid in roots:
            continue
        p = by_id[pid]
        lic = p.get("license") or (f"see {p['license_file']}" if p.get("license_file") else "UNKNOWN")
        pkgs.append({"name": p["name"], "version": p["version"], "license": lic,
                     "url": p.get("repository") or p.get("homepage") or f"https://crates.io/crates/{p['name']}",
                     "texts": license_files(Path(p["manifest_path"]).parent)})
    return pkgs


def md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(sections: dict[str, list[dict]]) -> str:
    total = sum(len(v) for v in sections.values())
    lines = [
        "# Third-party notices",
        "",
        f"<!-- lockfile-sha256: {lock_hashes()} -->",
        "<!-- Generated by scripts/gen_third_party_notices.py. Do not edit by hand. -->",
        "",
        "Logic-Loom itself is released under the MIT License (see `LICENSE`). The desktop",
        f"app also ships {total} third-party packages, listed below with their declared",
        "licenses, followed by the license and notice files those packages include.",
        "Identical texts are printed once, with every package that uses them.",
        "",
        "The frozen Python sidecar is built with PyInstaller, whose bootloader is",
        "distributed under the GPL-2.0-or-later with the PyInstaller bootloader exception,",
        "which permits bundling it with software under any license.",
        "",
    ]
    for title, pkgs in sections.items():
        lines += [f"- {title}: {len(pkgs)} packages"]
    lines.append("")
    groups: dict[str, list[str]] = defaultdict(list)
    first_name: dict[str, str] = {}
    for title, pkgs in sections.items():
        lines += [f"## {title}", "", "| Package | Version | License | Source |", "|---|---|---|---|"]
        for p in sorted(pkgs, key=lambda p: (p["name"].lower(), p["version"])):
            lines.append(f"| {md_cell(p['name'])} | {md_cell(p['version'])} | "
                         f"{md_cell(p['license'])} | {md_cell(p['url'])} |")
            for fname, text in p["texts"]:
                body = text.strip().replace("\r\n", "\n")
                if not body:
                    continue
                groups[body].append(f"{p['name']} {p['version']}")
                first_name.setdefault(body, fname)
        lines.append("")
    lines += ["## License and notice texts", ""]
    ordered = sorted(groups.items(), key=lambda kv: (sorted(set(kv[1]), key=str.lower)[0].lower(), kv[0]))
    for body, users in ordered:
        users = sorted(set(users), key=str.lower)
        lines += [f"### {first_name[body]}: {users[0]}" + (f" and {len(users) - 1} more" if len(users) > 1 else ""),
                  "", "Used by: " + ", ".join(users), "", "````text", body, "````", ""]
    missing = sorted(f"{p['name']} {p['version']}" for pkgs in sections.values() for p in pkgs if not p["texts"])
    if missing:
        lines += ["## Packages that ship no license file", "",
                  "These packages declare the license shown in the tables above but include no",
                  "license text in their distribution. The declared license applies.", "",
                  ", ".join(missing), ""]
    return "\n".join(lines)


def check() -> int:
    if not OUT.exists():
        print(f"{OUT.name} is missing; run: uv run python scripts/gen_third_party_notices.py")
        return 1
    m = HASH_LINE_RE.search(OUT.read_text(encoding="utf-8"))
    if not m or m.group(1) != lock_hashes():
        print(f"{OUT.name} is out of date with the lockfiles; run: "
              "uv run python scripts/gen_third_party_notices.py")
        return 1
    print(f"OK: {OUT.name} matches uv.lock, pnpm-lock.yaml and Cargo.lock")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="Only verify the file matches the lockfiles")
    args = ap.parse_args()
    if args.check:
        return check()
    sections = {
        "Python packages (sidecar)": python_packages(),
        "JavaScript packages (user interface)": js_packages(),
        "Rust crates (desktop shell)": rust_packages(),
    }
    OUT.write_text(render(sections), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}: " + ", ".join(f"{k}: {len(v)}" for k, v in sections.items()))
    unknown = [f"{p['name']} {p['version']}" for v in sections.values() for p in v if p["license"] == "UNKNOWN"]
    if unknown:
        print("no declared license: " + ", ".join(unknown), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
