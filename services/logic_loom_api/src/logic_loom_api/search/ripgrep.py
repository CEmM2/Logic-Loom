"""ripgrep backend: stateless, fast for keyword queries."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from . import SearchHit


class RipgrepBackend:
    name = "ripgrep"

    def __init__(self, vault_root: Path):
        self.vault_root = vault_root

    async def search(self, query: str, *, limit: int) -> list[SearchHit]:
        if not shutil.which("rg"):
            raise RuntimeError("ripgrep ('rg') not found on PATH")
        if not query.strip():
            return []
        proc = await asyncio.create_subprocess_exec(
            "rg",
            "--json",
            "--max-count",
            "5",
            "-e",
            query,
            str(self.vault_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        hits: list[SearchHit] = []
        for raw in stdout.decode("utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event["data"]
            path_text = data["path"].get("text") or data["path"].get("bytes")
            if not path_text:
                continue
            try:
                rel = Path(path_text).relative_to(self.vault_root).as_posix()
            except ValueError:
                rel = path_text
            line = int(data.get("line_number") or 0) or None
            snippet = (data.get("lines", {}).get("text") or "").rstrip("\n")
            hits.append(SearchHit(path=rel, line=line, snippet=snippet))
            if len(hits) >= limit:
                break
        return hits
