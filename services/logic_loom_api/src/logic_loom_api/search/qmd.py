"""QMD backend: relies on the user's existing QMD installation.

LogicLoom does **not** manage the QMD index lifecycle. The vault must already be
registered as a QMD collection (``qmd collection add <name> <vault_path>``).

Output of ``qmd search --json`` is a JSON array:
  [{"docid","score","file","title","snippet"}, ...]
where ``file`` is a ``qmd://<collection>/<rel-path>`` URI. We strip the
``qmd://<collection>/`` prefix to recover the vault-relative path. The
``--top N`` flag caps result count.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from . import SearchHit


class QmdBackend:
    name = "qmd"

    def __init__(self, vault_root: Path, collection: str | None):
        self.vault_root = vault_root
        self.collection = collection

    async def search(self, query: str, *, limit: int) -> list[SearchHit]:
        if not shutil.which("qmd"):
            raise RuntimeError("'qmd' CLI not found on PATH")
        if not self.collection:
            raise RuntimeError(
                "qmd backend requires `vault_qmd_collection` in settings — "
                "register the vault first with `qmd collection add <name> <vault_path>`."
            )
        if not query.strip():
            return []
        cmd = [
            "qmd",
            "search",
            "--collection",
            self.collection,
            "--top",
            str(limit),
            "--json",
            query,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"qmd search failed (rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:500]}"
            )
        try:
            data = json.loads(stdout.decode("utf-8", errors="replace") or "[]")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"qmd produced unparseable JSON: {e}") from e

        prefix = f"qmd://{self.collection}/"
        hits: list[SearchHit] = []
        for entry in data[:limit]:
            file_uri = entry.get("file") or ""
            rel = file_uri[len(prefix):] if file_uri.startswith(prefix) else file_uri
            # Drop the optional ":line" suffix qmd attaches.
            line: int | None = None
            if ":" in rel:
                rel, _, line_str = rel.rpartition(":")
                if line_str.isdigit():
                    line = int(line_str)
            hits.append(
                SearchHit(
                    path=rel,
                    line=line,
                    snippet=entry.get("snippet", ""),
                    score=float(entry["score"]) if entry.get("score") is not None else None,
                )
            )
        return hits
