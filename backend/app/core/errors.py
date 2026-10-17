"""Application errors and the JSON error envelope {code, message, details}."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger()


class ErrorResponse(BaseModel):
    """Error body returned by every failing endpoint."""

    code: str
    message: str
    details: Any = None


class AppError(Exception):
    """Base class for expected, client-facing errors."""

    status_code = 400
    code = "error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Create the error; `code` overrides the class default."""
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.details = details
        self.headers = headers or {}


class UnauthorizedError(AppError):
    """Missing or invalid credentials (401)."""

    status_code = 401
    code = "unauthorized"

    def __init__(self, message: str = "Not authenticated", **kwargs: Any) -> None:
        """Add the WWW-Authenticate challenge."""
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class ForbiddenError(AppError):
    """Authenticated but not allowed (403)."""

    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    """Resource missing or owned by another tenant (404)."""

    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    """State conflict such as a duplicate email (409)."""

    status_code = 409
    code = "conflict"


class UnprocessableError(AppError):
    """Semantically invalid input (422)."""

    status_code = 422
    code = "unprocessable"


class RateLimitedError(AppError):
    """Too many requests (429)."""

    status_code = 429
    code = "rate_limited"


def _response(
    status: int, code: str, message: str, details: Any, headers: Any = None
) -> JSONResponse:
    body = ErrorResponse(code=code, message=message, details=details).model_dump()
    return JSONResponse(status_code=status, content=body, headers=headers)


async def _app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return _response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Drop `input` and `ctx` so submitted secrets (passwords) are never echoed back.
    details = [
        {k: v for k, v in jsonable_encoder(err).items() if k not in {"input", "ctx"}}
        for err in exc.errors()
    ]
    return _response(422, "validation_error", "Request validation failed", details)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
    code = codes.get(exc.status_code, "http_error")
    return _response(exc.status_code, code, str(exc.detail), None, exc.headers)


async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_error", error=type(exc).__name__)
    return _response(500, "internal_error", "Internal server error", None)


def register_error_handlers(app: FastAPI) -> None:
    """Install handlers so every error uses the {code, message, details} shape."""
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled)
