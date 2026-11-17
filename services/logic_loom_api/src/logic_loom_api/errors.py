"""Uniform error envelope.

Shape: ``{"error": {"code": str, "message": str, "detail": dict | None}}``.

Every error response from logic_loom_api uses this envelope. Frontend code can
rely on the shape without dispatching on status code first.
"""

from __future__ import annotations

import errno
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .safe_paths import InvalidIdentifier

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
            # jsonable_encoder: a non-JSON body puts raw ``bytes`` in the
            # error's ``input``, which plain JSON encoding cannot serialise
            # (it used to turn every such request into a 500).
            detail={"errors": jsonable_encoder(exc.errors(), custom_encoder=_BYTES_ENCODER)},
        )
        return JSONResponse(body, status_code=422)

    @app.exception_handler(InvalidIdentifier)
    async def _invalid_identifier_handler(_request: Request, exc: InvalidIdentifier) -> JSONResponse:
        body = envelope(code="invalid_identifier", message=str(exc), detail=None)
        return JSONResponse(body, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        cid = getattr(request.state, "correlation_id", None)
        if _is_malformed_path_error(exc):
            # A NUL byte or an over-long name in a request-supplied id or path
            # reached a filesystem call. That is bad input, not a server fault.
            logger.info("rejected malformed path input (cid=%s): %s", cid, exc)
            body = envelope(
                code="invalid_identifier",
                message="A path or id in the request is malformed (NUL byte or too long).",
                detail={"correlation_id": cid},
            )
            return JSONResponse(body, status_code=422)
        logger.exception("unhandled error (cid=%s)", cid)
        # The traceback stays in the server log; the response carries only the
        # correlation id to find it by.
        body = envelope(
            code="internal_error",
            message="An unexpected error occurred. Check server logs.",
            detail={"correlation_id": cid},
        )
        return JSONResponse(body, status_code=500)


_BYTES_ENCODER = {bytes: lambda b: b.decode("utf-8", errors="replace")}


def _is_malformed_path_error(exc: BaseException) -> bool:
    if isinstance(exc, ValueError) and "null byte" in str(exc):
        return True
    return isinstance(exc, OSError) and exc.errno == errno.ENAMETOOLONG


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
