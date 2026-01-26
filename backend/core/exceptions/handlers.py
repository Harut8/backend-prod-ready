"""
FastAPI Exception Handlers.

Provides exception handlers for the FastAPI application.
Re-exports handlers from error_to_response for cleaner imports.
"""

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
from backend.core.exceptions.error_to_response import (
    bad_request_exception,
    conflict_exception,
    not_found_exception,
    server_error_exception,
    service_unavailable_exception,
    validation_exception,
)


logger = structlog.get_logger(__name__)


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
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"The requested resource '{path}' was not found.",
                    "context": {"path": path, "method": request.method},
                },
            },
        )
    elif status_code == 405:
        logger.info(
            "Method not allowed",
            path=path,
            method=request.method,
            client_host=request.client.host if request.client else None,
        )
        return JSONResponse(
            status_code=405,
            content={
                "success": False,
                "error": {
                    "code": "METHOD_NOT_ALLOWED",
                    "message": f"Method '{request.method}' is not allowed for '{path}'.",
                    "context": {"path": path, "method": request.method},
                },
            },
        )
    else:
        # Generic HTTP error handling
        logger.warning(
            "HTTP error",
            path=path,
            method=request.method,
            status_code=status_code,
            detail=detail,
            client_host=request.client.host if request.client else None,
        )
        return JSONResponse(
            status_code=status_code,
            content={
                "success": False,
                "error": {
                    "code": f"HTTP_{status_code}",
                    "message": str(detail) if detail else f"HTTP error {status_code}",
                },
            },
        )


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

    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "context": exc.context if exc.context else None,
            },
        },
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
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred. Please try again later.",
            },
        },
    )


# =============================================================================
# All Exports
# =============================================================================

__all__ = [
    # Re-exported from error_to_response
    "bad_request_exception",
    "conflict_exception",
    "domain_exception_handler",
    "http_exception_handler",
    "not_found_exception",
    "server_error_exception",
    "service_unavailable_exception",
    "unhandled_exception_handler",
    "validation_exception",
    # Primary handlers
    "validation_exception_handler",
]
