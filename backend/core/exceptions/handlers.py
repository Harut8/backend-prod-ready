"""
FastAPI Exception Handlers.

Provides exception handlers for the FastAPI application.
Re-exports handlers from error_to_response for cleaner imports.

Includes handlers for:
- Domain exceptions (business rule violations)
- HTTP exceptions (404, 405, etc.)
- Service exceptions (application-level HTTP errors)

Note: Identity Plan Kit (IPK) exceptions are handled by IPK itself
via register_error_handlers=True in the identity kit setup.
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
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
    Provides descriptive messages including the requested path.

    Note: IPK-specific errors are handled by identity-plan-kit's own handlers.
    """
    status_code = exc.status_code
    detail = exc.detail or ""
    path = request.url.path

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
# All Exports
# =============================================================================

__all__ = [
    "ServiceException",
    "bad_request_exception",
    "conflict_exception",
    "domain_exception_handler",
    "http_exception_handler",
    "not_found_exception",
    "server_error_exception",
    "service_exception_handler",
    "service_unavailable_exception",
    "unhandled_exception_handler",
    "validation_exception",
    "validation_exception_handler",
]
