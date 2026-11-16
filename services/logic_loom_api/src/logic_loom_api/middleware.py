"""Request-level middleware: correlation IDs."""

from __future__ import annotations

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("logic_loom_api.access")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a uuid4 to each request, expose it on ``request.state`` and as a response header."""

    HEADER = "X-Correlation-ID"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        cid = request.headers.get(self.HEADER) or uuid.uuid4().hex
        request.state.correlation_id = cid
        try:
            response = await call_next(request)
        except Exception:
            # Re-raise so the global exception handlers run; the cid is already on request.state.
            raise
        response.headers[self.HEADER] = cid
        return response
