"""
FastAPI Exception Handlers.

Provides exception handlers for the FastAPI application.
Re-exports handlers from error_to_response for cleaner imports.

Includes handlers for:
- Domain exceptions (business rule violations)
- HTTP exceptions (404, 405, etc.)
- Identity Plan Kit exceptions (auth, RBAC, plans)
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from identity_plan_kit.auth.domain.exceptions import (
    AuthError,
    TokenExpiredError,
    TokenInvalidError,
    UserInactiveError,
    UserNotFoundError as IPKUserNotFoundError,
)
from identity_plan_kit.plans.domain.exceptions import (
    FeatureNotAvailableError,
    PlanAuthorizationError,
    PlanExpiredError,
    PlanNotFoundError,
    QuotaExceededError,
    UserPlanNotFoundError,
)
from identity_plan_kit.rbac.domain.exceptions import (
    PermissionDeniedError,
    RoleNotFoundError,
)
from identity_plan_kit.shared.exceptions import BaseError as IPKBaseError
from starlette.exceptions import HTTPException
import structlog

from backend.core.domain.exceptions import (
    DomainAuthorizationError,
    DomainConflictError,
    DomainError,
    DomainStateError,
    DomainValidationError,
    EntityNotFoundError,
)
from backend.core.exceptions.error_codes import ErrorCode
from backend.core.exceptions.error_to_response import (
    bad_request_exception,
    conflict_exception,
    not_found_exception,
    server_error_exception,
    service_unavailable_exception,
    validation_exception,
)
from backend.core.exceptions.http_exceptions import ServiceException


# Re-export IPK exception types for use in main.py
IPK_EXCEPTION_HANDLERS = {
    "TokenExpiredError": TokenExpiredError,
    "TokenInvalidError": TokenInvalidError,
    "UserInactiveError": UserInactiveError,
    "IPKUserNotFoundError": IPKUserNotFoundError,
    "AuthError": AuthError,
    "PermissionDeniedError": PermissionDeniedError,
    "RoleNotFoundError": RoleNotFoundError,
    "QuotaExceededError": QuotaExceededError,
    "PlanExpiredError": PlanExpiredError,
    "PlanAuthorizationError": PlanAuthorizationError,
    "FeatureNotAvailableError": FeatureNotAvailableError,
    "UserPlanNotFoundError": UserPlanNotFoundError,
    "PlanNotFoundError": PlanNotFoundError,
    "IPKBaseError": IPKBaseError,
}

logger = structlog.get_logger(__name__)


def _build_error_response(
    status_code: int,
    code: str | ErrorCode,
    message: str,
    context: dict[str, Any] | None = None,
) -> JSONResponse:
    """
    Build a standard error response.

    Args:
        status_code: HTTP status code
        code: Error code (string or ErrorCode enum)
        message: Human-readable message
        context: Optional additional context

    Returns:
        JSONResponse in standard format:
        {"success": false, "error": {"code": "...", "message": "...", "context": {...}}}
    """
    error_content: dict[str, Any] = {
        "code": str(code),
        "message": message,
    }
    if context:
        error_content["context"] = context

    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": error_content,
        },
    )


# =============================================================================
# Re-exported Handlers (from error_to_response)
# =============================================================================

# These are async-compatible wrappers around the existing handlers
validation_exception_handler = validation_exception


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Handle Starlette/FastAPI HTTPException.

    Translates HTTPException to consistent error response format.
    Handles IPK auth errors that are converted to HTTPException in dependencies.
    Provides descriptive messages including the requested path.
    """
    status_code = exc.status_code
    detail = exc.detail or ""
    path = request.url.path

    # IPK converts domain exceptions to HTTPException in dependencies
    # Map known detail messages back to proper error codes
    ipk_error_mapping: dict[str, tuple[ErrorCode, str]] = {
        # Auth errors (401)
        "Token has expired": (ErrorCode.TOKEN_EXPIRED, "Your session has expired. Please log in again."),
        "Invalid token": (ErrorCode.TOKEN_INVALID, "Invalid authentication token."),
        "Not authenticated": (ErrorCode.UNAUTHORIZED, "Authentication required."),
        "User not found": (ErrorCode.USER_NOT_FOUND, "User not found."),
        # Refresh token errors (401)
        "Refresh token not provided": (
            ErrorCode.REFRESH_TOKEN_MISSING,
            "Refresh token not provided. Please log in again.",
        ),
        "Invalid refresh token": (ErrorCode.REFRESH_TOKEN_INVALID, "Invalid refresh token. Please log in again."),
        "Refresh token has expired": (
            ErrorCode.REFRESH_TOKEN_EXPIRED,
            "Refresh token has expired. Please log in again.",
        ),
        # OAuth errors (401)
        "OAuth authentication failed": (ErrorCode.OAUTH_ERROR, "OAuth authentication failed."),
        "Invalid OAuth state": (ErrorCode.OAUTH_STATE_INVALID, "Invalid OAuth state. Please try again."),
        "OAuth provider error": (ErrorCode.OAUTH_PROVIDER_ERROR, "OAuth provider error. Please try again."),
        # Auth errors (403)
        "User account is inactive": (ErrorCode.USER_INACTIVE, "Your account has been deactivated."),
        # Plan errors (403)
        "Plan has expired": (ErrorCode.PLAN_EXPIRED, "Your subscription plan has expired."),
        "No active plan found": (ErrorCode.USER_PLAN_NOT_FOUND, "No active subscription plan found."),
    }

    # Check for dynamic feature not available messages (format: "Feature 'X' not available")
    if detail and detail.startswith("Feature '") and detail.endswith("' not available"):
        feature_code = detail[9:-15]  # Extract feature code: "Feature '" = 9, "' not available" = 15
        logger.info(
            "Feature not available via HTTPException",
            path=path,
            method=request.method,
            feature=feature_code,
        )
        return _build_error_response(
            status_code=status_code,
            code=ErrorCode.FEATURE_NOT_AVAILABLE,
            message="This feature requires an upgraded plan.",
            context={"feature": feature_code},
        )

    # Check if this is a known IPK error
    if detail in ipk_error_mapping:
        code, message = ipk_error_mapping[detail]
        logger.info(
            "IPK auth error via HTTPException",
            path=path,
            method=request.method,
            error_code=code,
        )
        return _build_error_response(status_code=status_code, code=code, message=message)

    # Map status codes to descriptive error responses
    if status_code == 404:
        logger.info(
            "Resource not found",
            path=path,
            method=request.method,
            client_host=request.client.host if request.client else None,
        )
        return _build_error_response(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message=f"The requested resource '{path}' was not found.",
            context={"path": path, "method": request.method},
        )
    if status_code == 405:
        logger.info(
            "Method not allowed",
            path=path,
            method=request.method,
            client_host=request.client.host if request.client else None,
        )
        return _build_error_response(
            status_code=405,
            code=ErrorCode.METHOD_NOT_ALLOWED,
            message=f"Method '{request.method}' is not allowed for '{path}'.",
            context={"path": path, "method": request.method},
        )
    # Generic HTTP error handling
    logger.warning(
        "HTTP error",
        path=path,
        method=request.method,
        status_code=status_code,
        detail=detail,
        client_host=request.client.host if request.client else None,
    )
    return _build_error_response(
        status_code=status_code,
        code=f"HTTP_{status_code}",  # Dynamic code for unknown HTTP errors
        message=str(detail) if detail else f"HTTP error {status_code}",
    )


# =============================================================================
# Service Exception Handler
# =============================================================================


async def service_exception_handler(request: Request, exc: ServiceException) -> JSONResponse:
    """
    Handle application ServiceException and its subclasses.

    These are our application's HTTP exceptions (AuthenticationFailedError,
    NotFoundError, etc.) which extend ServiceException.

    Returns responses in our standard format with success: false.
    """
    logger.info(
        "Service exception",
        path=request.url.path,
        method=request.method,
        status_code=exc.status_code,
        error_code=str(exc.code),
    )
    return exc.to_response()  # type: ignore [no-any-return]


# =============================================================================
# Domain Exception Handler
# =============================================================================


async def domain_exception_handler(request: Request, exc: DomainError) -> JSONResponse:
    """
    Handle domain-level exceptions.

    Translates pure domain exceptions to appropriate HTTP responses.
    Maps exception types to HTTP status codes:
    - DomainValidationError -> 400 Bad Request
    - EntityNotFoundError -> 404 Not Found
    - DomainConflictError -> 409 Conflict
    - DomainAuthorizationError -> 403 Forbidden
    - DomainStateError -> 422 Unprocessable Entity
    - DomainError (base) -> 400 Bad Request
    """
    # Determine HTTP status code based on exception type
    if isinstance(exc, EntityNotFoundError):
        status_code = 404
    elif isinstance(exc, DomainConflictError):
        status_code = 409
    elif isinstance(exc, DomainAuthorizationError):
        status_code = 403
    elif isinstance(exc, DomainStateError):
        status_code = 422
    elif isinstance(exc, DomainValidationError):
        status_code = 400
    else:
        status_code = 400

    # Log the domain error
    logger.warning(
        "Domain error",
        path=request.url.path,
        method=request.method,
        error_code=exc.code,
        error_message=exc.message,
        error_context=exc.context,
        status_code=status_code,
    )

    return _build_error_response(
        status_code=status_code,
        code=exc.code,
        message=exc.message,
        context=exc.context if exc.context else None,
    )


# =============================================================================
# Unhandled Exception Handler
# =============================================================================


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle any unhandled exceptions.

    This is the catch-all handler that ensures no exception goes unhandled.
    Logs the full exception with traceback for debugging.

    Returns a generic 500 response without exposing internal details.
    """
    # Log with full traceback for debugging
    logger.exception(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        client_host=request.client.host if request.client else None,
        exception_type=type(exc).__name__,
        exception_message=str(exc),
    )

    # Return generic error response (don't expose internal details)
    return _build_error_response(
        status_code=500,
        code=ErrorCode.INTERNAL_SERVER_ERROR,
        message="An unexpected error occurred. Please try again later.",
    )


# =============================================================================
# Identity Plan Kit Exception Handlers
# =============================================================================


async def token_expired_handler(request: Request, exc: TokenExpiredError) -> JSONResponse:
    """Handle expired token errors from IPK."""
    logger.info(
        "Token expired",
        path=request.url.path,
        method=request.method,
    )
    return _build_error_response(
        status_code=401,
        code=exc.code,
        message="Your session has expired. Please log in again.",
    )


async def token_invalid_handler(request: Request, exc: TokenInvalidError) -> JSONResponse:
    """Handle invalid token errors from IPK."""
    logger.warning(
        "Invalid token",
        path=request.url.path,
        method=request.method,
    )
    return _build_error_response(
        status_code=401,
        code=exc.code,
        message="Invalid authentication token.",
    )


async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    """Handle general authentication errors from IPK."""
    logger.warning(
        "Authentication error",
        path=request.url.path,
        method=request.method,
        error_code=exc.code,
    )
    return _build_error_response(
        status_code=401,
        code=exc.code,
        message=exc.message,
        context=exc.details if exc.details else None,
    )


async def user_inactive_handler(request: Request, exc: UserInactiveError) -> JSONResponse:
    """Handle inactive user errors from IPK."""
    logger.warning(
        "Inactive user access attempt",
        path=request.url.path,
        method=request.method,
    )
    return _build_error_response(
        status_code=403,
        code=exc.code,
        message="Your account has been deactivated.",
    )


async def ipk_user_not_found_handler(request: Request, exc: IPKUserNotFoundError) -> JSONResponse:
    """Handle user not found errors from IPK."""
    logger.info(
        "IPK user not found",
        path=request.url.path,
        method=request.method,
    )
    return _build_error_response(
        status_code=404,
        code=exc.code,
        message="User not found.",
    )


async def permission_denied_handler(request: Request, exc: PermissionDeniedError) -> JSONResponse:
    """Handle permission denied errors from IPK RBAC."""
    logger.warning(
        "Permission denied",
        path=request.url.path,
        method=request.method,
        permission=exc.permission_code,
    )
    return _build_error_response(
        status_code=403,
        code=exc.code,
        message=exc.message,
        context={"permission": exc.permission_code} if exc.permission_code else None,
    )


async def role_not_found_handler(request: Request, exc: RoleNotFoundError) -> JSONResponse:
    """Handle role not found errors from IPK RBAC."""
    logger.warning(
        "Role not found",
        path=request.url.path,
        method=request.method,
    )
    return _build_error_response(
        status_code=404,
        code=exc.code,
        message=exc.message,
    )


async def quota_exceeded_handler(request: Request, exc: QuotaExceededError) -> JSONResponse:
    """Handle quota exceeded errors from IPK plans."""
    logger.info(
        "Quota exceeded",
        path=request.url.path,
        method=request.method,
        feature=exc.feature_code,
        limit=exc.limit,
        used=exc.used,
    )
    return _build_error_response(
        status_code=429,
        code=exc.code,
        message=exc.message,
        context={
            "feature": exc.feature_code,
            "limit": exc.limit,
            "used": exc.used,
            "period": exc.period,
        },
    )


async def plan_expired_handler(request: Request, exc: PlanExpiredError) -> JSONResponse:
    """Handle plan expired errors from IPK.

    Returns 402 Payment Required - the appropriate status code for expired
    subscriptions/plans where payment is needed to restore access.
    """
    logger.info(
        "Plan expired",
        path=request.url.path,
        method=request.method,
    )
    return _build_error_response(
        status_code=402,
        code=exc.code,
        message="Your subscription plan has expired. Please renew to continue.",
    )


async def feature_not_available_handler(request: Request, exc: FeatureNotAvailableError) -> JSONResponse:
    """Handle feature not available errors from IPK plans."""
    logger.info(
        "Feature not available",
        path=request.url.path,
        method=request.method,
        feature=exc.feature_code,
        plan=exc.plan_code,
    )
    return _build_error_response(
        status_code=403,
        code=exc.code,
        message=exc.message,
        context={
            "feature": exc.feature_code,
            "plan": exc.plan_code,
        },
    )


async def user_plan_not_found_handler(request: Request, exc: UserPlanNotFoundError) -> JSONResponse:
    """Handle user plan not found errors from IPK."""
    logger.info(
        "User plan not found",
        path=request.url.path,
        method=request.method,
    )
    return _build_error_response(
        status_code=404,
        code=exc.code,
        message="No active subscription plan found.",
    )


async def plan_not_found_handler(request: Request, exc: PlanNotFoundError) -> JSONResponse:
    """Handle plan not found errors from IPK."""
    logger.info(
        "Plan not found",
        path=request.url.path,
        method=request.method,
    )
    return _build_error_response(
        status_code=404,
        code=exc.code,
        message=exc.message,
    )


async def plan_authorization_error_handler(request: Request, exc: PlanAuthorizationError) -> JSONResponse:
    """Handle plan authorization errors from IPK.

    Raised when a plan operation is not authorized (e.g., unauthorized plan assignment).
    """
    logger.warning(
        "Plan authorization error",
        path=request.url.path,
        method=request.method,
        operation=exc.operation,
        target_user_id=exc.target_user_id,
        caller_user_id=exc.caller_user_id,
    )
    return _build_error_response(
        status_code=403,
        code=exc.code,
        message=exc.message,
        context={
            "operation": exc.operation,
            "target_user_id": exc.target_user_id,
        },
    )


async def ipk_base_error_handler(request: Request, exc: IPKBaseError) -> JSONResponse:
    """Handle base IPK errors (catch-all for IPK domain errors)."""
    logger.warning(
        "IPK base error",
        path=request.url.path,
        method=request.method,
        error_code=exc.code,
        error_message=exc.message,
    )
    return _build_error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        context=exc.details if exc.details else None,
    )


# =============================================================================
# All Exports
# =============================================================================

__all__ = [
    "ServiceException",
    "auth_error_handler",
    "bad_request_exception",
    "conflict_exception",
    "domain_exception_handler",
    "feature_not_available_handler",
    "http_exception_handler",
    "ipk_base_error_handler",
    "ipk_user_not_found_handler",
    "not_found_exception",
    "permission_denied_handler",
    "plan_authorization_error_handler",
    "plan_expired_handler",
    "plan_not_found_handler",
    "quota_exceeded_handler",
    "role_not_found_handler",
    "server_error_exception",
    "service_exception_handler",
    "service_unavailable_exception",
    "token_expired_handler",
    "token_invalid_handler",
    "unhandled_exception_handler",
    "user_inactive_handler",
    "user_plan_not_found_handler",
    "validation_exception",
    "validation_exception_handler",
]
