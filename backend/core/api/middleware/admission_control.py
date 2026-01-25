"""
Admission Control Middleware for preventing connection pool exhaustion.

This middleware uses a semaphore to limit the number of concurrent requests
being processed by the application. When the limit is reached, new requests
are rejected with a 503 Service Unavailable response.

This is critical for preventing:
- Database connection pool exhaustion under burst traffic
- Cascading failures when downstream services are slow
- Memory exhaustion from too many concurrent requests
"""

import asyncio

from backend.core.conf.settings import SETTINGS
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import structlog


logger = structlog.get_logger(__name__)


class AdmissionControlMiddleware(BaseHTTPMiddleware):
    """
    Semaphore-based admission control to prevent resource exhaustion.

    Limits the number of concurrent requests being processed. When the limit
    is reached, new requests receive a 503 response with Retry-After header.

    Configuration:
        SETTINGS.APP.MAX_CONCURRENT_REQUESTS: Maximum concurrent requests (default: 100)

    Health check endpoints (/health, /ready, /live) are exempt from admission control
    to ensure monitoring can always reach the application.
    """

    # Endpoints exempt from admission control (health checks must always work)
    EXEMPT_PATHS = frozenset({"/health", "/ready", "/live", "/metrics"})

    def __init__(self, app, max_concurrent: int | None = None) -> None:  # type: ignore[no-untyped-def]  # noqa: ANN001
        super().__init__(app)
        self._max_concurrent = max_concurrent or SETTINGS.APP.MAX_CONCURRENT_REQUESTS
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._current_requests = 0
        self._total_rejected = 0

        logger.info(
            "Admission control middleware initialized",
            max_concurrent_requests=self._max_concurrent,
        )

    @property
    def current_requests(self) -> int:
        """Current number of requests being processed."""
        return self._current_requests

    @property
    def total_rejected(self) -> int:
        """Total number of requests rejected due to admission control."""
        return self._total_rejected

    def _is_exempt(self, path: str) -> bool:
        """Check if path is exempt from admission control."""
        # Strip API prefix if present
        clean_path = path.rstrip("/")
        # Check if any exempt path is at the end of the URL path
        return any(clean_path.endswith(exempt) for exempt in self.EXEMPT_PATHS)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN201
        # Always allow health check endpoints through
        if self._is_exempt(request.url.path):
            return await call_next(request)

        # Try to acquire semaphore without blocking
        acquired = self._semaphore.locked() is False

        if not acquired:
            # Check if we can acquire without waiting
            try:
                # Use wait_for with 0 timeout to check without blocking
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=0.001,  # Near-instant timeout
                )
                acquired = True
            except TimeoutError:
                acquired = False

        if not acquired:
            # Server is at capacity - reject request
            self._total_rejected += 1
            logger.warning(
                "Request rejected - server at capacity",
                path=request.url.path,
                method=request.method,
                current_requests=self._current_requests,
                max_concurrent=self._max_concurrent,
                total_rejected=self._total_rejected,
            )
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Service temporarily unavailable - server at capacity",
                    "retry_after": 5,
                },
                headers={"Retry-After": "5"},
            )

        # Semaphore acquired - process request
        self._current_requests += 1
        try:
            response: Response = await call_next(request)
            return response
        finally:
            self._current_requests -= 1
            self._semaphore.release()
