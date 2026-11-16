"""MkDocs hooks for the Logic-Loom docs site (wired in ``mkdocs.yml``).

The Changelog page is the root ``CHANGELOG.md``. Its links are written for
reading on GitHub, relative to the repository root (``./docs/release_notes/…``),
so this hook includes the file itself and rewrites those links to be relative
to ``docs/changelog.md``, where the site serves it. ``mkdocs build --strict``
then validates them like any other page link.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_DOCS_LINK = re.compile(r"\]\((?:\./)?docs/")


def on_page_markdown(markdown, page, config, files):  # noqa: ARG001 - mkdocs hook signature
    if page.file.src_uri != "changelog.md":
        return markdown
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    text = _DOCS_LINK.sub("](", text)
    # A link to the release-notes folder lands on its index page.
    return text.replace("](release_notes/)", "](release_notes/index.md)")
