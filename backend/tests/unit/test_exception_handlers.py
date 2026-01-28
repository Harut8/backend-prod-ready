"""
Unit tests for exception handlers.

Tests that IPK exceptions are properly mapped to HTTP responses.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions.handlers import (
    plan_authorization_error_handler,
    plan_expired_handler,
    quota_exceeded_handler,
    token_expired_handler,
    feature_not_available_handler,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request."""
    request = MagicMock()
    request.url.path = "/api/v1/test"
    request.method = "POST"
    return request


class TestPlanAuthorizationErrorHandler:
    """Tests for PlanAuthorizationError handler."""

    async def test_returns_403_status(self, mock_request):
        """Handler should return 403 Forbidden status."""
        from identity_plan_kit.plans.domain.exceptions import PlanAuthorizationError

        exc = PlanAuthorizationError(
            message="Not authorized to assign plan",
            operation="assign_plan",
            target_user_id="user-123",
            caller_user_id="caller-456",
        )

        response = await plan_authorization_error_handler(mock_request, exc)

        assert response.status_code == 403

    async def test_includes_operation_in_context(self, mock_request):
        """Handler should include operation details in error context."""
        from identity_plan_kit.plans.domain.exceptions import PlanAuthorizationError
        import json

        exc = PlanAuthorizationError(
            message="Not authorized to assign plan",
            operation="assign_plan",
            target_user_id="user-123",
            caller_user_id=None,
        )

        response = await plan_authorization_error_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert body["error"]["code"] == "PLAN_AUTHORIZATION_ERROR"
        assert body["error"]["context"]["operation"] == "assign_plan"
        assert body["error"]["context"]["target_user_id"] == "user-123"


class TestQuotaExceededHandler:
    """Tests for QuotaExceededError handler."""

    async def test_returns_429_status(self, mock_request):
        """Handler should return 429 Too Many Requests status."""
        from identity_plan_kit.plans.domain.exceptions import QuotaExceededError

        exc = QuotaExceededError(
            feature_code="ai_generation",
            limit=100,
            used=100,
            period="monthly",
        )

        response = await quota_exceeded_handler(mock_request, exc)

        assert response.status_code == 429

    async def test_includes_quota_details(self, mock_request):
        """Handler should include quota details in response."""
        from identity_plan_kit.plans.domain.exceptions import QuotaExceededError
        import json

        exc = QuotaExceededError(
            feature_code="ai_generation",
            limit=100,
            used=150,
            period="monthly",
        )

        response = await quota_exceeded_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert body["error"]["code"] == "QUOTA_EXCEEDED"
        assert body["error"]["context"]["limit"] == 100
        assert body["error"]["context"]["used"] == 150


class TestTokenExpiredHandler:
    """Tests for TokenExpiredError handler."""

    async def test_returns_401_status(self, mock_request):
        """Handler should return 401 Unauthorized status."""
        from identity_plan_kit.auth.domain.exceptions import TokenExpiredError

        exc = TokenExpiredError()

        response = await token_expired_handler(mock_request, exc)

        assert response.status_code == 401

    async def test_error_code_is_token_expired(self, mock_request):
        """Handler should return TOKEN_EXPIRED error code."""
        from identity_plan_kit.auth.domain.exceptions import TokenExpiredError
        import json

        exc = TokenExpiredError()

        response = await token_expired_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert body["error"]["code"] == "TOKEN_EXPIRED"


class TestFeatureNotAvailableHandler:
    """Tests for FeatureNotAvailableError handler."""

    async def test_returns_403_status(self, mock_request):
        """Handler should return 403 Forbidden status."""
        from identity_plan_kit.plans.domain.exceptions import FeatureNotAvailableError

        exc = FeatureNotAvailableError(
            feature_code="premium_feature",
            plan_code="free",
        )

        response = await feature_not_available_handler(mock_request, exc)

        assert response.status_code == 403

    async def test_includes_feature_and_plan(self, mock_request):
        """Handler should include feature and plan in context."""
        from identity_plan_kit.plans.domain.exceptions import FeatureNotAvailableError
        import json

        exc = FeatureNotAvailableError(
            feature_code="premium_feature",
            plan_code="free",
        )

        response = await feature_not_available_handler(mock_request, exc)
        body = json.loads(response.body.decode())

        assert body["error"]["context"]["feature"] == "premium_feature"
        assert body["error"]["context"]["plan"] == "free"


class TestPlanExpiredHandler:
    """Tests for PlanExpiredError handler."""

    async def test_returns_402_status(self, mock_request):
        """Handler should return 402 Payment Required status."""
        from identity_plan_kit.plans.domain.exceptions import PlanExpiredError

        exc = PlanExpiredError()

        response = await plan_expired_handler(mock_request, exc)

        assert response.status_code == 402
