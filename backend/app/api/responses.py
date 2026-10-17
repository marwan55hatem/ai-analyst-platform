"""Shared OpenAPI response declarations."""

from typing import Any

from app.core.errors import ErrorResponse

UNAUTHORIZED: dict[int | str, dict[str, Any]] = {401: {"model": ErrorResponse}}
FORBIDDEN: dict[int | str, dict[str, Any]] = {403: {"model": ErrorResponse}}
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}}
CONFLICT: dict[int | str, dict[str, Any]] = {409: {"model": ErrorResponse}}
UNPROCESSABLE: dict[int | str, dict[str, Any]] = {422: {"model": ErrorResponse}}
RATE_LIMITED: dict[int | str, dict[str, Any]] = {429: {"model": ErrorResponse}}
