"""Guards for turning request-supplied ids and paths into filesystem paths.

Two checks, used together wherever an id from a request becomes a file name:

* :func:`safe_id` — an allowlist for the id itself (letters, digits, ``.``,
  ``_``, ``-``; starts with a letter or digit; at most 128 characters). That
  rules out separators, ``..``, NUL bytes and over-long names before any
  filesystem call sees them.
* :func:`ensure_under` — the resolved target must stay inside its root, as
  defence in depth against anything the allowlist misses (symlinked parents,
  platform quirks).

Both raise :class:`InvalidIdentifier`, a ``ValueError`` subclass that the error
handlers in :mod:`logic_loom_api.errors` turn into a 422.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable
from pathlib import Path

MAX_ID_LENGTH = 128
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class InvalidIdentifier(ValueError):
    """A request-supplied id or path that must not reach the filesystem."""


def safe_id(value: str, *, kind: str = "id") -> str:
    """Return ``value`` unchanged if it is a filename-safe id, else raise."""
    if not isinstance(value, str) or not value:
        raise InvalidIdentifier(f"{kind} is required")
    if len(value) > MAX_ID_LENGTH:
        raise InvalidIdentifier(f"{kind} is longer than {MAX_ID_LENGTH} characters")
    if not _SAFE_ID.match(value):
        raise InvalidIdentifier(
            f"invalid {kind} {value[:64]!r}: use letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return value


def ensure_under(root: Path, target: Path, *, kind: str = "path") -> Path:
    """Return ``target`` if its resolved form is inside ``root``, else raise."""
    try:
        resolved_root = root.expanduser().resolve()
        resolved = target.expanduser().resolve()
    except (OSError, ValueError) as exc:  # NUL bytes, name too long, loops
        raise InvalidIdentifier(f"invalid {kind}: {exc}") from exc
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise InvalidIdentifier(f"{kind} is outside its allowed folder")
    return target


def resolve_within(raw: str, roots: Iterable[Path], *, kind: str = "path") -> Path:
    """Resolve a user-supplied absolute path and require it under one of ``roots``.

    Returns the resolved path. Raises :class:`InvalidIdentifier` for malformed
    input (NUL bytes, over-long names) and for paths outside every root.
    """
    if "\x00" in raw:
        raise InvalidIdentifier(f"invalid {kind}: contains a NUL byte")
    try:
        resolved = Path(raw).expanduser().resolve()
    except (OSError, ValueError) as exc:
        raise InvalidIdentifier(f"invalid {kind}: {exc}") from exc
    for root in roots:
        try:
            r = root.expanduser().resolve()
        except (OSError, ValueError):
            continue
        if resolved == r or r in resolved.parents:
            return resolved
    raise InvalidIdentifier(f"{kind} is outside the configured folders")


def split_command(command: str) -> list[str]:
    """Split a user-configured command line into an argv list.

    POSIX rules everywhere except Windows, where backslashes are path
    separators: there ``shlex`` runs in non-POSIX mode and the quotes it keeps
    around a token (``"C:\\Program Files\\x.exe"``) are stripped. Raises
    ``ValueError`` on unbalanced quotes.
    """
    if os.name != "nt":
        return shlex.split(command)
    parts = shlex.split(command, posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]

