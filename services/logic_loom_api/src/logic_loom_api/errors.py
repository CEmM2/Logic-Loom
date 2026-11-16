"""Uniform error envelope.

Shape: ``{"error": {"code": str, "message": str, "detail": dict | None}}``.

Every error response from logic_loom_api uses this envelope. Frontend code can
rely on the shape without dispatching on status code first.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def envelope(code: str, message: str, detail: Any = None) -> dict[str, dict[str, Any]]:
    return {"error": {"code": code, "message": message, "detail": detail}}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        body = envelope(
            code=getattr(exc, "code", None) or _default_code(exc.status_code),
            message=str(exc.detail) if exc.detail is not None else _default_message(exc.status_code),
            detail=None,
        )
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        body = envelope(
            code="validation_error",
            message="One or more request fields failed validation.",
            detail={"errors": exc.errors()},
        )
        return JSONResponse(body, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        cid = getattr(request.state, "correlation_id", None)
        logger.exception("unhandled error (cid=%s)", cid)
        body = envelope(
            code="internal_error",
            message="An unexpected error occurred. Check server logs.",
            detail={"correlation_id": cid, "traceback": traceback.format_exc().splitlines()[-5:]},
        )
        return JSONResponse(body, status_code=500)


def _default_code(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        412: "precondition_failed",
        422: "unprocessable_entity",
    }.get(status, "http_error")


def _default_message(status: int) -> str:
    return {
        400: "Bad request.",
        401: "Unauthorized.",
        403: "Forbidden.",
        404: "Not found.",
        409: "Conflict.",
        412: "Precondition failed.",
        422: "Unprocessable entity.",
    }.get(status, f"HTTP {status}.")


# Convenience for handlers that want to raise an HTTPException with a stable code.
def http_error(status: int, code: str, message: str, detail: Any = None) -> HTTPException:
    exc = HTTPException(status_code=status, detail=message)
    setattr(exc, "code", code)
    if detail is not None:
        setattr(exc, "extra_detail", detail)
    return exc
