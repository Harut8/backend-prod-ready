"""
Unit tests for exception handlers.

Tests that HTTP exceptions are properly mapped to HTTP responses
with correct error codes and messages.

Note: IPK (Identity Plan Kit) exceptions are now handled by IPK itself
via register_error_handlers=True. Those handlers are tested within IPK.
"""

import json
from unittest.mock import MagicMock

import pytest
from starlette.exceptions import HTTPException

from backend.core.exceptions.handlers import (
    domain_exception_handler,
    http_exception_handler,
    service_exception_handler,
    unhandled_exception_handler,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request."""
    request = MagicMock()
    request.url.path = "/api/v1/test"
    request.method = "POST"
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


# =============================================================================
# HTTP Exception Handler Tests - Status Code Handling
# =============================================================================


class TestHttpExceptionHandlerStatusCodes:
    """Tests for HTTP status code handling in http_exception_handler."""

    async def test_404_not_found(self, mock_request):
        """404 should return NOT_FOUND with path context."""
        exc = HTTPException(status_code=404, detail="Not Found")

        response = await http_exception_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert response.status_code == 404
        assert body["error"]["code"] == "NOT_FOUND"
        assert "/api/v1/test" in body["error"]["message"]
        assert body["error"]["context"]["path"] == "/api/v1/test"
        assert body["error"]["context"]["method"] == "POST"

    async def test_405_method_not_allowed(self, mock_request):
        """405 should return METHOD_NOT_ALLOWED with method and path context."""
        exc = HTTPException(status_code=405, detail="Method Not Allowed")

        response = await http_exception_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert response.status_code == 405
        assert body["error"]["code"] == "METHOD_NOT_ALLOWED"
        assert "POST" in body["error"]["message"]
        assert "/api/v1/test" in body["error"]["message"]


class TestHttpExceptionHandlerGenericFallback:
    """Tests for generic HTTP error fallback in http_exception_handler."""

    async def test_unknown_error_uses_http_status_code(self, mock_request):
        """Unknown errors should use HTTP_XXX code format."""
        exc = HTTPException(status_code=418, detail="I'm a teapot")

        response = await http_exception_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert response.status_code == 418
        assert body["error"]["code"] == "HTTP_418"
        assert body["error"]["message"] == "I'm a teapot"

    async def test_empty_detail_uses_generic_message(self, mock_request):
        """Empty detail should use generic HTTP error message."""
        exc = HTTPException(status_code=500, detail="")

        response = await http_exception_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert response.status_code == 500
        assert body["error"]["code"] == "HTTP_500"
        assert body["error"]["message"] == "HTTP error 500"

    async def test_none_detail_uses_generic_message(self, mock_request):
        """None detail should use generic HTTP error message."""
        exc = HTTPException(status_code=503, detail=None)

        response = await http_exception_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert response.status_code == 503
        assert body["error"]["code"] == "HTTP_503"
        # Starlette sets default detail to status phrase, so we accept either
        assert body["error"]["message"] in ["HTTP error 503", "Service Unavailable"]


# =============================================================================
# Domain Exception Handler Tests
# =============================================================================


class TestDomainExceptionHandler:
    """Tests for domain exception handler."""

    async def test_validation_error_returns_400(self, mock_request):
        """DomainValidationError should return 400 Bad Request."""
        from backend.core.domain.exceptions import DomainValidationError

        exc = DomainValidationError(
            message="Invalid email format",
            field="email",
        )

        response = await domain_exception_handler(mock_request, exc)

        assert response.status_code == 400

    async def test_entity_not_found_returns_404(self, mock_request):
        """EntityNotFoundError should return 404 Not Found."""
        from backend.core.domain.exceptions import EntityNotFoundError

        exc = EntityNotFoundError(
            entity_type="User",
            entity_id="user-123",
        )

        response = await domain_exception_handler(mock_request, exc)

        assert response.status_code == 404

    async def test_conflict_error_returns_409(self, mock_request):
        """DomainConflictError should return 409 Conflict."""
        from backend.core.domain.exceptions import DomainConflictError

        exc = DomainConflictError(
            message="Email already exists",
            entity_type="User",
        )

        response = await domain_exception_handler(mock_request, exc)

        assert response.status_code == 409

    async def test_authorization_error_returns_403(self, mock_request):
        """DomainAuthorizationError should return 403 Forbidden."""
        from backend.core.domain.exceptions import DomainAuthorizationError

        exc = DomainAuthorizationError(
            message="Not allowed to perform this action",
            action="delete",
        )

        response = await domain_exception_handler(mock_request, exc)

        assert response.status_code == 403

    async def test_state_error_returns_422(self, mock_request):
        """DomainStateError should return 422 Unprocessable Entity."""
        from backend.core.domain.exceptions import DomainStateError

        exc = DomainStateError(
            message="Cannot cancel completed order",
            current_state="completed",
        )

        response = await domain_exception_handler(mock_request, exc)

        assert response.status_code == 422

    async def test_includes_context_in_response(self, mock_request):
        """Handler should include exception context in response."""
        from backend.core.domain.exceptions import DomainValidationError

        exc = DomainValidationError(
            message="Invalid email format",
            field="email",
            value="invalid",
        )

        response = await domain_exception_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert body["error"]["context"]["field"] == "email"
        assert body["error"]["context"]["value"] == "invalid"


# =============================================================================
# Service Exception Handler Tests
# =============================================================================


class TestServiceExceptionHandler:
    """Tests for ServiceException handler."""

    async def test_returns_exception_status_code(self, mock_request):
        """Handler should return the exception's status code."""
        from backend.core.exceptions.http_exceptions import NotFoundError

        exc = NotFoundError(message="Resource not found")

        response = await service_exception_handler(mock_request, exc)

        assert response.status_code == 404

    async def test_returns_exception_error_code(self, mock_request):
        """Handler should return the exception's error code."""
        from backend.core.exceptions.http_exceptions import AuthenticationFailedError

        exc = AuthenticationFailedError(message="Invalid credentials")

        response = await service_exception_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert body["error"]["code"] == "AUTHENTICATION_FAILED"


# =============================================================================
# Unhandled Exception Handler Tests
# =============================================================================


class TestUnhandledExceptionHandler:
    """Tests for unhandled exception handler."""

    async def test_returns_500_status(self, mock_request):
        """Handler should return 500 Internal Server Error."""
        exc = RuntimeError("Something went wrong")

        response = await unhandled_exception_handler(mock_request, exc)

        assert response.status_code == 500

    async def test_returns_internal_server_error_code(self, mock_request):
        """Handler should return INTERNAL_SERVER_ERROR code."""
        exc = ValueError("Unexpected error")

        response = await unhandled_exception_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert body["success"] is False
        assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
        # Should not expose internal error details
        assert "Unexpected error" not in body["error"]["message"]

    async def test_does_not_expose_internal_details(self, mock_request):
        """Handler should not expose internal error details."""
        exc = Exception("Database connection failed: password authentication failed")

        response = await unhandled_exception_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert "password" not in body["error"]["message"].lower()
        assert "database" not in body["error"]["message"].lower()


# =============================================================================
# Response Format Tests
# =============================================================================


class TestResponseFormat:
    """Tests to ensure all handlers return consistent response format."""

    async def test_all_responses_have_success_false(self, mock_request):
        """All error responses should have success: false."""
        handlers_and_exceptions = [
            (http_exception_handler, HTTPException(status_code=404)),
            (unhandled_exception_handler, RuntimeError("test")),
        ]

        for handler, exc in handlers_and_exceptions:
            response = await handler(mock_request, exc)
            body = json.loads(response.body.decode())
            assert body["success"] is False, f"Handler {handler.__name__} did not return success: false"

    async def test_all_responses_have_error_object(self, mock_request):
        """All error responses should have an error object with code and message."""
        handlers_and_exceptions = [
            (http_exception_handler, HTTPException(status_code=400, detail="Bad request")),
            (unhandled_exception_handler, RuntimeError("test")),
        ]

        for handler, exc in handlers_and_exceptions:
            response = await handler(mock_request, exc)
            body = json.loads(response.body.decode())
            assert "error" in body, f"Handler {handler.__name__} missing error object"
            assert "code" in body["error"], f"Handler {handler.__name__} missing error code"
            assert "message" in body["error"], f"Handler {handler.__name__} missing error message"
