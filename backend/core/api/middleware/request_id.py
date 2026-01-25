import uuid

# Import from centralized observability module - single source of truth
from backend.core.observability.logging import request_id_ctx_var
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import structlog


logger = structlog.get_logger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Middleware that generates or extracts a request ID for correlation tracking.

    The request ID is:
    - Extracted from X-Request-ID header if present
    - Generated as a new UUID if not present
    - Added to response headers as X-Request-ID
    - Stored in context variable for access by logging system
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN201
        # Get or generate request ID
        _request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Store in context variable for logging
        request_id_ctx_var.set(_request_id)

        # Add to request state for access in handlers
        request.state.request_id = _request_id

        # Process request
        try:
            response: Response = await call_next(request)
        except Exception:
            # Log unexpected middleware errors
            logger.exception(
                "Request processing failed in middleware",
                request_id=_request_id,
                path=request.url.path,
                method=request.method,
            )
            raise

        # Add request ID to response headers for client tracking
        response.headers["X-Request-ID"] = _request_id

        return response
