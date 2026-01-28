"""
Unit tests for RequestTimeoutMiddleware.

Tests critical paths:
- Requests that complete within timeout succeed
- Requests that exceed timeout return 504
- Health check endpoints are exempt from timeout
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import JSONResponse, Response

from backend.core.api.middleware.request_timeout import RequestTimeoutMiddleware


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def create_mock_request(path: str = "/api/test", method: str = "GET") -> MagicMock:
    """Create a mock Starlette request."""
    request = MagicMock()
    request.url = MagicMock()
    request.url.path = path
    request.method = method
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


class TestRequestTimeoutMiddleware:
    """Tests for request timeout enforcement."""

    async def test_fast_request_succeeds(self):
        """Request completing within timeout should succeed."""
        app = MagicMock()
        middleware = RequestTimeoutMiddleware(app, timeout=5.0)

        request = create_mock_request()

        async def fast_handler(req):
            await asyncio.sleep(0.01)  # Very fast
            return Response(content="OK", status_code=200)

        response = await middleware.dispatch(request, fast_handler)

        assert response.status_code == 200

    async def test_slow_request_times_out(self):
        """Request exceeding timeout should return 504."""
        app = MagicMock()
        middleware = RequestTimeoutMiddleware(app, timeout=0.1)  # 100ms timeout

        request = create_mock_request()

        async def slow_handler(req):
            await asyncio.sleep(1.0)  # Takes 1 second (exceeds timeout)
            return Response(content="OK", status_code=200)

        response = await middleware.dispatch(request, slow_handler)

        assert response.status_code == 504
        assert isinstance(response, JSONResponse)

    async def test_timeout_response_includes_retry_after_header(self):
        """504 response should include Retry-After header."""
        app = MagicMock()
        middleware = RequestTimeoutMiddleware(app, timeout=0.05)

        request = create_mock_request()

        async def slow_handler(req):
            await asyncio.sleep(1.0)
            return Response(content="OK", status_code=200)

        response = await middleware.dispatch(request, slow_handler)

        assert response.status_code == 504
        assert "Retry-After" in response.headers

    async def test_health_endpoint_exempt_from_timeout(self):
        """Health check endpoints should not have timeout applied."""
        app = MagicMock()
        middleware = RequestTimeoutMiddleware(app, timeout=0.01)  # Very short timeout

        for exempt_path in ["/health", "/ready", "/live", "/metrics"]:
            request = create_mock_request(path=f"/api/v1{exempt_path}")

            async def handler(req):
                await asyncio.sleep(0.1)  # Would exceed timeout if not exempt
                return Response(content="OK", status_code=200)

            response = await middleware.dispatch(request, handler)
            assert response.status_code == 200, f"Path {exempt_path} should be exempt"

    async def test_custom_timeout_value(self):
        """Middleware should use provided timeout value."""
        app = MagicMock()
        custom_timeout = 0.2
        middleware = RequestTimeoutMiddleware(app, timeout=custom_timeout)

        assert middleware._timeout == custom_timeout

    async def test_is_exempt_matches_path_suffix(self):
        """Exempt check should match path suffixes."""
        app = MagicMock()
        middleware = RequestTimeoutMiddleware(app, timeout=1.0)

        # Should be exempt
        assert middleware._is_exempt("/health") is True
        assert middleware._is_exempt("/api/v1/health") is True
        assert middleware._is_exempt("/ready") is True
        assert middleware._is_exempt("/api/v1/ready") is True
        assert middleware._is_exempt("/live") is True
        assert middleware._is_exempt("/metrics") is True

        # Should NOT be exempt
        assert middleware._is_exempt("/api/users") is False
        assert middleware._is_exempt("/api/health/check") is False  # health is not suffix
        assert middleware._is_exempt("/healthcheck") is False


class TestRequestTimeoutMiddlewareEdgeCases:
    """Edge case tests for request timeout middleware."""

    async def test_cancelled_request_propagates_cancellation(self):
        """Cancelled requests should propagate CancelledError."""
        app = MagicMock()
        middleware = RequestTimeoutMiddleware(app, timeout=5.0)

        request = create_mock_request()

        async def cancelling_handler(req):
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await middleware.dispatch(request, cancelling_handler)

    async def test_timeout_error_message_includes_context(self):
        """Timeout error response should include helpful context."""
        app = MagicMock()
        middleware = RequestTimeoutMiddleware(app, timeout=0.05)

        request = create_mock_request()

        async def slow_handler(req):
            await asyncio.sleep(1.0)
            return Response(content="OK", status_code=200)

        response = await middleware.dispatch(request, slow_handler)

        assert response.status_code == 504

        # Parse response body
        body = response.body.decode()
        assert "REQUEST_TIMEOUT" in body
        assert "timeout" in body.lower()

    async def test_middleware_uses_settings_default_timeout(self):
        """Should use timeout from settings if not provided."""
        app = MagicMock()

        with patch("backend.core.api.middleware.request_timeout.SETTINGS") as mock_settings:
            mock_settings.TIMEOUTS.REQUEST_TIMEOUT = 42.0
            middleware = RequestTimeoutMiddleware(app)

            assert middleware._timeout == 42.0
