"""Comment-preserving zsum.config.yaml editor. Mirrors plugin_dev's
ZsumConfigDocumentService: read the YAML, apply a flat list of dotted-path
updates, write atomically. Only paths in the updates list are touched —
everything else (comments, ordering, blank lines) is preserved by ruamel.
"""

from __future__ import annotations

import io as _io
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096  # don't auto-wrap our long string values
    return y


class ConfigDocState(BaseModel):
    path: str
    exists: bool
    raw: str
    parse_error: str | None
    parsed: Any


def read_doc(path: Path) -> ConfigDocState:
    if not path.exists():
        return ConfigDocState(
            path=str(path),
            exists=False,
            raw="",
            parse_error=None,
            parsed=None,
        )
    raw = path.read_text(encoding="utf-8")
    if raw.strip() == "":
        return ConfigDocState(path=str(path), exists=True, raw=raw, parse_error=None, parsed={})
    try:
        # Round-trip load just to surface parse errors with line info; we
        # also re-load for write later (cheaper than caching across requests).
        parsed = _yaml().load(raw)
    except Exception as e:
        return ConfigDocState(
            path=str(path),
            exists=True,
            raw=raw,
            parse_error=str(e),
            parsed=None,
        )
    if not isinstance(parsed, dict):
        return ConfigDocState(
            path=str(path),
            exists=True,
            raw=raw,
            parse_error="Config root must be a YAML mapping.",
            parsed=None,
        )
    # Convert CommentedMap → plain dict for JSON transport (UI never sees
    # ruamel internals — it sends back updates by dotted path).
    return ConfigDocState(
        path=str(path),
        exists=True,
        raw=raw,
        parse_error=None,
        parsed=_to_plain(parsed),
    )


def _to_plain(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _to_plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_to_plain(v) for v in node]
    return node


def _set_in(root: CommentedMap, tokens: list[str], value: Any) -> None:
    """Mutate ``root`` so that ``root[t0][t1]…[tn] = value``, creating
    intermediate maps as ``CommentedMap`` so ruamel keeps round-tripping
    safely. Mirrors yaml.Document.setIn from the JS lib."""
    cur: Any = root
    for tok in tokens[:-1]:
        if tok not in cur or not isinstance(cur[tok], dict):
            cur[tok] = CommentedMap()
        cur = cur[tok]
    cur[tokens[-1]] = value


class Update(BaseModel):
    path: str  # dotted, e.g. "llm.routing.cards"
    value: Any  # any YAML-serializable value (str, int, float, bool, list, None)


def write_doc(path: Path, updates: list[Update]) -> ConfigDocState:
    """Apply ``updates`` to the YAML at ``path`` and write atomically.

    If the file doesn't exist or is empty, start from an empty mapping.
    """
    yaml = _yaml()
    if path.exists() and path.read_text(encoding="utf-8").strip() != "":
        try:
            doc = yaml.load(path.read_text(encoding="utf-8"))
        except Exception as e:  # pragma: no cover — surfaced via state
            return ConfigDocState(
                path=str(path),
                exists=True,
                raw=path.read_text(encoding="utf-8"),
                parse_error=str(e),
                parsed=None,
            )
        if doc is None:
            doc = CommentedMap()
        if not isinstance(doc, dict):
            return ConfigDocState(
                path=str(path),
                exists=True,
                raw=path.read_text(encoding="utf-8"),
                parse_error="Config root must be a YAML mapping.",
                parsed=None,
            )
    else:
        doc = CommentedMap()

    for u in updates:
        tokens = u.path.split(".")
        if not tokens or any(not t for t in tokens):
            raise ValueError(f"Invalid dotted path: {u.path!r}")
        _set_in(doc, tokens, u.value)

    buf = _io.StringIO()
    yaml.dump(doc, buf)
    serialized = buf.getvalue()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, path)
    return read_doc(path)
