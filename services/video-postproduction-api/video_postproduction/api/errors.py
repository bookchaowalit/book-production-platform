"""Shared API errors and safe response handlers."""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("video_postproduction.api")


class APIError(Exception):
    """A domain error that can be rendered without leaking internals."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _request_id(request: Request) -> str:
    return request.headers.get("x-request-id") or f"req_{uuid4().hex}"


def _payload(request: Request, code: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "request_id": _request_id(request)}}


async def api_error_handler(request: Request, error: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=_payload(request, error.code, error.message),
    )


async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_payload(request, "request_validation_error", "The request payload is invalid."),
    )


async def http_error_handler(request: Request, error: StarletteHTTPException) -> JSONResponse:
    status_messages = {
        401: ("unauthorized", "Authentication is required."),
        404: ("not_found", "The requested resource was not found."),
        405: ("method_not_allowed", "The requested method is not allowed."),
    }
    code, message = status_messages.get(error.status_code, ("http_error", "The request could not be completed."))
    return JSONResponse(status_code=error.status_code, content=_payload(request, code, message))


async def internal_error_handler(request: Request, error: Exception) -> JSONResponse:
    logger.error("unhandled_api_error type=%s", type(error).__name__)
    return JSONResponse(
        status_code=500,
        content=_payload(request, "internal_error", "The request could not be completed."),
    )
