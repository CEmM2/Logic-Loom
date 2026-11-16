#!/usr/bin/env python3
"""Logic-Loom's documentation build.

The public documentation site lives in `docs/` and is built by MkDocs
(`mkdocs build --strict`, see mkdocs.yml and .github/workflows/docs.yml),
which already fails on broken links and anchors inside the site. This script
covers what MkDocs does not see: the root markdown files
(README/CLAUDE/CHANGELOG/AGENTS) and the `dev/` reference tree (architecture
digests, release notes, the STATUS ledger, contributor docs). Most of `dev/`
(plans, tasks, chats, handoffs, session logs) is a process archive that is
not meant to stay pristine, so it is out of scope.

It is a deterministic, dependency-free check that a maintainer can
run in one command and that CI runs on every PR touching docs. It verifies,
across the *current, reader-facing* documentation surface (see SCOPE
below):

  1. Every relative markdown link (`[text](target)`, including image links)
     resolves to a real file or directory, and if it carries a `#fragment`,
     that fragment matches a real heading in the target (or the current
     file, for same-document fragments).
  2. Every static HTML guide is well-formed: every non-void element is
     explicitly opened and closed, tags nest correctly, and no closing tag
     is left dangling or missing at end of file.
  3. Every in-page anchor link (`href="#id"`) in an HTML guide resolves to
     a real `id="..."` in that same document — these guides use `#id`
     hrefs for their own table-of-contents nav, and a typo there is
     invisible until a reader actually clicks it.

None of this needs a new dependency — it is stdlib-only, matching how this repo's
other `scripts/*.py` helpers are invoked (`uv run python scripts/...`).

Usage:
    uv run python scripts/check_docs.py

Exits 0 with a summary line when the whole scope is clean, non-zero with
every failure printed (file, and what's wrong) otherwise.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The current, reader-facing documentation surface. Deliberately excludes
# dev/plans, dev/tasks, dev/chats, dev/handoffs, dev/reports, dev/notes,
# dev/tracking, dev/reminders, dev/design_docs, and dev/session-log.md —
# that is a process archive (103+219+... files), not documentation meant to
# stay correct going forward. If a file there needs fixing, fix it because
# it is wrong, not because this build failed on it.
SCOPE_ROOTS = [
    ROOT / "README.md",
    ROOT / "CLAUDE.md",
    ROOT / "CHANGELOG.md",
    ROOT / "AGENTS.md",
    ROOT / "dev" / "STATUS.md",
    ROOT / "dev" / "docs",
    ROOT / "dev" / "architecture",
]

MD_SUFFIXES = {".md", ".markdown"}
HTML_SUFFIXES = {".html", ".htm"}

VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# `[text](target)` and `![alt](target)` — same existence rule for both.
MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s][^)]*)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Failure:
    path: Path
    message: str


@dataclass
class Report:
    files_checked: int = 0
    failures: list[Failure] = field(default_factory=list)

    def fail(self, path: Path, message: str) -> None:
        self.failures.append(Failure(path, message))

    @property
    def ok(self) -> bool:
        return not self.failures


def iter_scope_files() -> list[Path]:
    files: set[Path] = set()
    for entry in SCOPE_ROOTS:
        if entry.is_dir():
            for suffix in MD_SUFFIXES | HTML_SUFFIXES:
                files.update(entry.rglob(f"*{suffix}"))
        elif entry.is_file():
            files.add(entry)
        # A configured root that doesn't exist yet is not a build failure
        # here — it would just mean nothing to check under it.
    return sorted(files)


def slugify(heading: str) -> str:
    """GitHub-style heading slug: lowercase, strip punctuation, spaces -> hyphens."""
    heading = re.sub(r"`([^`]*)`", r"\1", heading)  # unwrap inline code
    heading = heading.strip().lower()
    heading = re.sub(r"[^\w\s-]", "", heading)
    heading = re.sub(r"\s+", "-", heading).strip("-")
    return heading


def extract_md_heading_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if not m:
            continue
        slug = slugify(m.group(2))
        if not slug:
            continue
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 0
        slugs.add(slug)
    return slugs


_HEADING_CACHE: dict[Path, set[str]] = {}


def heading_slugs_for(path: Path) -> set[str]:
    if path not in _HEADING_CACHE:
        _HEADING_CACHE[path] = extract_md_heading_slugs(
            path.read_text(encoding="utf-8", errors="replace")
        )
    return _HEADING_CACHE[path]


def check_markdown_links(path: Path, report: Report) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    own_slugs = extract_md_heading_slugs(text)

    # Strip fenced code blocks before scanning for links so example
    # markdown/shell snippets containing "](" don't get treated as links.
    scan_text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)

    for match in MD_LINK_RE.finditer(scan_text):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "mailto:", "tel:")):
            continue
        if " " in target and not target.startswith("<"):
            # Markdown link with a title, e.g. (target "title") — take the
            # path/fragment portion only.
            target = target.split(" ", 1)[0]
        target = target.strip("<>")

        if target.startswith("#"):
            frag = slugify(target[1:])
            if frag and frag not in own_slugs:
                report.fail(
                    path,
                    f"dangling same-document anchor '{target}' — no heading "
                    f"slugifies to '{frag}'",
                )
            continue

        file_part, _, frag = target.partition("#")
        resolved = (path.parent / file_part).resolve()
        if not resolved.exists():
            report.fail(
                path, f"link target does not exist: '{target}' -> {resolved}"
            )
            continue

        if frag and resolved.is_file() and resolved.suffix in MD_SUFFIXES:
            target_slugs = heading_slugs_for(resolved)
            wanted = slugify(frag)
            if wanted and wanted not in target_slugs:
                report.fail(
                    path,
                    f"dangling anchor '#{frag}' in link to "
                    f"'{file_part}' — no heading there slugifies to '{wanted}'",
                )


class _StructuralHTMLChecker(HTMLParser):
    """Validates tag balance/nesting and collects ids + in-page anchor hrefs.

    Deliberately stricter than an HTML5 auto-close-aware parser: every
    non-void element must be explicitly closed and tags must nest properly.
    That is a reasonable bar for hand-authored, self-contained guide pages
    (and it is exactly the class of mistake ("malformed HTML") this build
    is meant to catch), even though a browser would silently recover from
    some violations of it.
    """

    def __init__(self, path: Path, report: Report) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.report = report
        self.stack: list[tuple[str, int]] = []
        self.ids: set[str] = set()
        self.anchor_hrefs: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if attr_map.get("id"):
            self.ids.add(attr_map["id"])
        href = attr_map.get("href")
        if href and href.startswith("#") and len(href) > 1:
            self.anchor_hrefs.append((href[1:], self.getpos()[0]))
        if tag not in VOID_ELEMENTS:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_ELEMENTS:
            return  # some authors "close" void elements; harmless either way
        if not self.stack:
            self.report.fail(
                self.path,
                f"line {self.getpos()[0]}: closing tag </{tag}> with no open tag",
            )
            return
        open_tag, open_line = self.stack[-1]
        if open_tag == tag:
            self.stack.pop()
            return
        # Mismatch: look deeper in the stack for a matching open tag, which
        # means everything above it was left unclosed.
        for depth, (candidate, cand_line) in enumerate(reversed(self.stack)):
            if candidate == tag:
                unclosed = self.stack[len(self.stack) - depth :]
                names = ", ".join(f"<{t}> (line {ln})" for t, ln in unclosed[:-1])
                self.report.fail(
                    self.path,
                    f"line {self.getpos()[0]}: </{tag}> closes <{open_tag}> "
                    f"opened at line {open_line} out of order"
                    + (f"; also left unclosed: {names}" if names else ""),
                )
                del self.stack[len(self.stack) - depth - 1 :]
                return
        self.report.fail(
            self.path,
            f"line {self.getpos()[0]}: closing tag </{tag}> does not match "
            f"any open tag (innermost open: <{open_tag}> at line {open_line})",
        )

    def error(self, message: str) -> None:  # pragma: no cover - py<3.10 shim
        self.report.fail(self.path, f"parser error: {message}")


def check_html(path: Path, report: Report) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    checker = _StructuralHTMLChecker(path, report)
    checker.feed(text)
    checker.close()
    if checker.stack:
        names = ", ".join(f"<{t}> (line {ln})" for t, ln in checker.stack)
        report.fail(path, f"unclosed tag(s) at end of file: {names}")
    for anchor, line in checker.anchor_hrefs:
        if anchor not in checker.ids:
            report.fail(
                path,
                f"line {line}: href=\"#{anchor}\" has no matching id=\"{anchor}\" "
                f"in this document",
            )


def main() -> int:
    report = Report()
    for path in iter_scope_files():
        rel = path.relative_to(ROOT)
        report.files_checked += 1
        if path.suffix in MD_SUFFIXES:
            check_markdown_links(path, report)
        elif path.suffix in HTML_SUFFIXES:
            check_html(path, report)

    if report.failures:
        print(f"docs build FAILED — {len(report.failures)} issue(s) across "
              f"{report.files_checked} file(s) checked:\n")
        for f in report.failures:
            print(f"  {f.path.relative_to(ROOT)}: {f.message}")
        return 1

    print(f"docs build passed — {report.files_checked} file(s) checked, "
          f"0 issues.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
