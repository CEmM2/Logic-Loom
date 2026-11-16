"""Per-session preview tokens for the four migration import endpoints.

The workspace import endpoints (``import-akms-repo``, ``import-plan``,
``import-akms-claude-assets``, ``import-zsum-prompts``) each support three
modes: ``preview`` (no writes), ``merge`` (write only non-conflicting
records), and ``replace`` (overwrite existing records).

**Invariant**: ``mode="replace"`` MUST present a valid ``preview_token``
returned by an earlier ``mode="preview"`` call for the same endpoint.
Without a valid token, the route returns HTTP 400. This is the last line
of defence against an accidental destructive import from the UI or a
mistyped curl invocation.

Tokens are:

* **Per-endpoint** — a token minted by ``import-akms-repo``'s preview is
  not accepted by ``import-plan``'s replace, and vice-versa.
* **Short-lived** — default TTL is 5 minutes from mint. The store
  expires entries lazily on lookup.
* **Single-use** — a successful ``replace`` consumes the token; replaying
  the same token on a second ``replace`` returns 400.
* **In-memory, per-process** — the registry is intentionally not
  persisted. A sidecar restart invalidates all outstanding tokens. This
  matches the design decision in Phase_12_context_summary.md.

The store is process-global (module-level singleton) — the four import
routes are stateless functions that share the same registry. Concurrent
mints are safe; we use ``secrets.token_urlsafe`` so collisions are
astronomically unlikely and we hold the registry lock only across the
dict mutation.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any


# Token lifetime in seconds. Five minutes is generous for a human-driven
# preview → replace round trip and short enough that a stale token from a
# previous session can't accidentally authorise a destructive operation.
TOKEN_TTL_SECONDS: float = 5 * 60.0


@dataclass(frozen=True)
class _TokenEntry:
    endpoint: str
    expires_at: float
    payload: dict[str, Any]


class ImportTokenRegistry:
    """In-memory registry of preview-tokens keyed by endpoint id.

    Thread-safe via a single ``threading.RLock``. The registry is small
    (one entry per active preview), so a flat dict is fine.
    """

    def __init__(self, ttl_seconds: float = TOKEN_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.RLock()
        self._tokens: dict[str, _TokenEntry] = {}

    def mint(self, endpoint: str, payload: dict[str, Any] | None = None) -> str:
        """Mint a new preview token for ``endpoint``.

        Returns the opaque token string. ``payload`` is optional
        endpoint-specific context (e.g. a diff summary hash) that the
        validate step can cross-check; for the simple invariant the four
        Phase-12 endpoints enforce, we just need the token to exist.
        """
        token = secrets.token_urlsafe(24)
        entry = _TokenEntry(
            endpoint=endpoint,
            expires_at=time.monotonic() + self._ttl,
            payload=dict(payload or {}),
        )
        with self._lock:
            self._tokens[token] = entry
        return token

    def consume(self, endpoint: str, token: str | None) -> _TokenEntry:
        """Validate and consume ``token`` for ``endpoint``.

        Raises :class:`ImportTokenError` (subclass of ``ValueError``) if:

        * ``token`` is missing or empty.
        * ``token`` is unknown to this process.
        * ``token`` was minted for a different endpoint.
        * ``token`` has expired (older than :data:`TOKEN_TTL_SECONDS`).

        On success the entry is removed from the registry (single-use).
        """
        if not token:
            raise ImportTokenError("preview_token is required for mode=replace")
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None:
                raise ImportTokenError("preview_token is unknown or already consumed")
            # Expiry check uses monotonic clock so wall-clock skew is irrelevant.
            if time.monotonic() > entry.expires_at:
                # Drop the stale entry while we hold the lock.
                self._tokens.pop(token, None)
                raise ImportTokenError("preview_token has expired; run mode=preview again")
            if entry.endpoint != endpoint:
                raise ImportTokenError(
                    f"preview_token was minted for {entry.endpoint!r}, not {endpoint!r}"
                )
            # Consume — single-use semantics.
            self._tokens.pop(token, None)
            return entry

    def clear(self) -> None:
        """Drop every outstanding token. Used by tests for isolation."""
        with self._lock:
            self._tokens.clear()


class ImportTokenError(ValueError):
    """Token validation failed. Callers map this to HTTP 400."""


# Process-global registry instance.
_REGISTRY = ImportTokenRegistry()


def registry() -> ImportTokenRegistry:
    """Return the process-global token registry singleton."""
    return _REGISTRY
