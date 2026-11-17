"""Local API authentication: per-launch token + Host allowlist.

The sidecar listens on a loopback port, but loopback is not a trust boundary:
any local process can connect, and a web page can reach it through DNS
rebinding (an attacker-controlled hostname that resolves to 127.0.0.1, which
makes the page same-origin with the API and sidesteps CORS entirely).

When the Tauri shell launches the sidecar it generates a random token and
passes it in ``LOGIC_LOOM_API_TOKEN``. With a token configured, every request
must:

* carry a ``Host`` header naming a loopback address (``127.0.0.1``,
  ``localhost`` or ``[::1]``, any port) — this is what defeats DNS rebinding,
  because the rebinding page's requests still say ``Host: evil.example``;
* present the token, either as the ``X-Logic-Loom-Token`` header or, for
  ``EventSource`` streams and plain links that cannot set headers, as the
  ``llt`` query parameter.

``GET /api/app/health`` stays open (it reveals nothing) and CORS preflight
``OPTIONS`` requests never reach this middleware, because ``CORSMiddleware``
wraps it and answers them itself.

Without ``LOGIC_LOOM_API_TOKEN`` (``uv run logic-loom-api --port 17321`` for
standalone debugging, the test suite) the middleware is a no-op and a warning
is logged once at startup. The packaged app always sets the token.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("logic_loom_api.auth")

TOKEN_ENV = "LOGIC_LOOM_API_TOKEN"
TOKEN_HEADER = "x-logic-loom-token"
TOKEN_QUERY_PARAM = "llt"
# Shortest token accepted from the environment. The shell sends 64 hex chars.
MIN_TOKEN_LENGTH = 32

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
OPEN_PATHS = frozenset({"/api/app/health"})


_captured_token: str | None = None


def capture_token_from_env() -> str | None:
    """Read the token once and remove it from ``os.environ``.

    Called by the console entry point before the app is built, so the secret
    is not inherited by the subprocesses the sidecar spawns (zsum, node-gen,
    agent CLIs), which have no business calling the API.
    """
    global _captured_token
    _captured_token = configured_token()
    os.environ.pop(TOKEN_ENV, None)
    return _captured_token


def configured_token() -> str | None:
    """Return the token, or ``None`` when auth is off.

    Prefers a token already captured by :func:`capture_token_from_env`,
    otherwise reads the environment.

    A token shorter than :data:`MIN_TOKEN_LENGTH` is refused outright (the
    sidecar would otherwise run with a guessable secret), so startup fails
    loudly instead of silently weakening the check.
    """
    if _captured_token is not None:
        return _captured_token
    raw = os.environ.get(TOKEN_ENV, "").strip()
    if not raw:
        return None
    if len(raw) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            f"{TOKEN_ENV} must be at least {MIN_TOKEN_LENGTH} characters long"
        )
    return raw


def host_is_allowed(host_header: str) -> bool:
    """True when ``host_header`` names a loopback host (any port, or none)."""
    host = host_header.strip().lower()
    if not host:
        return False
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return False
        name = host[: end + 1]
        rest = host[end + 1 :]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return False
        return name in ALLOWED_HOSTS
    name, sep, port = host.partition(":")
    if sep and not port.isdigit():
        return False
    return name in ALLOWED_HOSTS


class LocalAuthMiddleware:
    """Pure ASGI middleware (streams pass through untouched)."""

    def __init__(self, app: ASGIApp, token: str | None = None) -> None:
        self.app = app
        self.token = token if token is not None else configured_token()
        if self.token is None:
            logger.warning(
                "%s is not set: the local API accepts unauthenticated requests. "
                "This is expected only for standalone debugging; the desktop app "
                "always sets it.",
                TOKEN_ENV,
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.token is None or scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # No websocket routes exist; refuse anything that is not plain HTTP
            # rather than teach this middleware a second protocol.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

        if not host_is_allowed(headers.get("host", "")):
            await _reject(send, 421, "host_not_allowed", "Requests must address the local API by a loopback host.")
            return

        if scope.get("path", "") in OPEN_PATHS:
            await self.app(scope, receive, send)
            return

        presented = headers.get(TOKEN_HEADER)
        if presented is None:
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            values = query.get(TOKEN_QUERY_PARAM)
            presented = values[0] if values else None

        if presented is None or not hmac.compare_digest(presented.encode(), self.token.encode()):
            await _reject(send, 401, "unauthorized", "Missing or invalid local API token.")
            return

        await self.app(scope, receive, send)


async def _reject(send: Send, status: int, code: str, message: str) -> None:
    # Same envelope shape as errors.envelope(), built inline so this module has
    # no import-time dependency on the FastAPI error handlers.
    body = json.dumps({"error": {"code": code, "message": message, "detail": None}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
