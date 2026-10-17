"""HTTP middleware."""

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request id to logs and the response; honour an inbound header."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Bind the request id for the duration of the request."""
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        structlog.get_logger().info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            tenant_id=getattr(request.state, "tenant_id", None),
        )
        return response
