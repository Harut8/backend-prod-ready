"""
Request Timeout Middleware for preventing runaway requests.

This middleware enforces a global timeout on all requests to prevent:
- Slow clients holding connections indefinitely
- Runaway requests consuming resources
- Connection pool exhaustion from long-running operations

Note: This is a safety net - individual operations should have their own
timeouts at the repository/service level for more granular control.
"""

import asyncio

from backend.core.conf.settings import SETTINGS
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import structlog


logger = structlog.get_logger(__name__)


class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """
    Enforces a global timeout on all HTTP requests.

    If a request takes longer than the configured timeout, it will be
    cancelled and a 504 Gateway Timeout response will be returned.

    Configuration:
        SETTINGS.TIMEOUTS.REQUEST_TIMEOUT: Maximum request duration in seconds (default: 30)

    Health check endpoints are exempt from the timeout to ensure
    monitoring can always get a response, even during high load.
    """

    # Endpoints exempt from request timeout (health checks should always respond)
    EXEMPT_PATHS = frozenset({"/health", "/ready", "/live", "/metrics"})

    def __init__(
        self,
        app: FastAPI,
        timeout: float | None = None,
    ) -> None:
        super().__init__(app)
        self._timeout = timeout or SETTINGS.TIMEOUTS.REQUEST_TIMEOUT

        logger.info(
            "Request timeout middleware initialized",
            timeout_seconds=self._timeout,
        )

    def _is_exempt(self, path: str) -> bool:
        """Check if path is exempt from request timeout."""
        clean_path = path.rstrip("/")
        return any(clean_path.endswith(exempt) for exempt in self.EXEMPT_PATHS)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN201
        """Process request with timeout enforcement."""
        # Health check endpoints are exempt
        if self._is_exempt(request.url.path):
            return await call_next(request)

        try:
            # Use asyncio.wait_for for proper cancellation on timeout
            response = await asyncio.wait_for(
                call_next(request),
                timeout=self._timeout,
            )

        except TimeoutError:
            # Log the timeout with request context
            logger.warning(
                "Request timed out",
                path=request.url.path,
                method=request.method,
                timeout_seconds=self._timeout,
                client_ip=request.client.host if request.client else "unknown",
            )

            # Return 504 Gateway Timeout with retry guidance
            return JSONResponse(
                status_code=504,
                content={
                    "success": False,
                    "error": {
                        "code": "REQUEST_TIMEOUT",
                        "message": "Request timed out. Please try again.",
                        "context": {
                            "timeout_seconds": self._timeout,
                            "suggestion": "The server took too long to respond. "
                            "Try again or reduce the complexity of your request.",
                        },
                    },
                },
                headers={
                    "Retry-After": "5",
                },
            )

        except asyncio.CancelledError:
            # Request was cancelled (e.g., client disconnected)
            logger.debug(
                "Request cancelled",
                path=request.url.path,
                method=request.method,
            )
            raise
        else:
            return response
