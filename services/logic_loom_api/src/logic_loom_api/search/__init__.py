"""Pluggable vault search.

Two backends:
  - ``ripgrep`` — stateless, shells out to ``rg --json``.
  - ``qmd``    — uses the user's existing QMD installation; requires the
                 vault to be registered as a QMD collection.

Both implement the ``SearchBackend`` protocol below. Backends are picked
per-request via ``?backend=…`` (defaults to ``Settings.search_backend``).
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class SearchHit(BaseModel):
    path: str
    line: int | None = None
    snippet: str
    score: float | None = None


class SearchBackend(Protocol):
    name: str

    async def search(self, query: str, *, limit: int) -> list[SearchHit]: ...
