from typing import Any

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from backend.core.exceptions.error_codes import ErrorCode


class ServiceException(HTTPException):
    """Base exception class for all service-level exceptions.

    This exception integrates with FastAPI's exception handling and provides
    a consistent error response format across the application.
    """

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "Internal Server Error"
    code = ErrorCode.INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str | None = None,
        code: str | ErrorCode | None = None,
        errors: Any = None,
        status_code: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Initialize service exception with custom parameters.

        Args:
            message: Override default error message
            code: Override default error code
            errors: Additional error details (e.g., validation errors)
            status_code: Override default HTTP status code
            meta: Additional metadata (always instance-specific, never shared)
        """
        # Always create instance-specific meta dict to avoid mutable default anti-pattern
        self.meta = meta or {}

        if message:
            self.message = message
        if status_code:
            self.status_code = status_code

        self.payload = {"message": self.message}

        if errors:
            self.payload["errors"] = errors

        if code:
            self.code = code

        # Convert ErrorCode enum to string for JSON serialization
        if self.code:
            self.payload["code"] = str(self.code)

        super().__init__(status_code=self.status_code, detail=self.payload)

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f"{self.__class__.__name__}(code={self.code}, status_code={self.status_code}, message={self.message!r})"

    def to_response(self) -> JSONResponse:
        """Convert exception to JSONResponse in standard API format.

        Returns:
            JSONResponse with the exception details in the format:
            {
                "success": false,
                "error": {
                    "code": "ERROR_CODE",
                    "message": "Human-readable message",
                    "context": { ... }  # optional
                }
            }
        """
        error_content: dict[str, Any] = {
            "code": str(self.code),
            "message": self.message,
        }

        # Build context from meta and errors
        context: dict[str, Any] = {}
        if self.meta:
            context.update(self.meta)
        if "errors" in self.payload:
            context["errors"] = self.payload["errors"]

        if context:
            error_content["context"] = context

        return JSONResponse(
            status_code=self.status_code,
            content={
                "success": False,
                "error": error_content,
            },
        )


class RequestError(ServiceException):
    """HTTP 400 Bad Request exception.

    Used when the client's request is malformed or contains invalid parameters.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    message = "Bad request"
    code = ErrorCode.BAD_REQUEST


class AuthenticationFailedError(ServiceException):
    """HTTP 401 Unauthorized exception.

    Used when authentication credentials are missing, invalid, or expired.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    message = "Authentication Failed."
    code = ErrorCode.AUTHENTICATION_FAILED


class PermissionDeniedError(ServiceException):
    """HTTP 403 Forbidden exception.

    Used when the authenticated user lacks permission to access the resource.
    """

    status_code = status.HTTP_403_FORBIDDEN
    message = "You do not have permission to perform this action."
    code = ErrorCode.PERMISSION_DENIED


class NotFoundError(ServiceException):
    """HTTP 404 Not Found exception.

    Used when the requested resource does not exist.
    """

    status_code = status.HTTP_404_NOT_FOUND
    message = "Resource Not Found"
    code = ErrorCode.NOT_FOUND


class MethodNotAllowedError(ServiceException):
    """HTTP 405 Method Not Allowed exception.

    Used when the HTTP method is not supported for the endpoint.
    """

    status_code = status.HTTP_405_METHOD_NOT_ALLOWED
    message = "Method Not Allowed"
    code = ErrorCode.METHOD_NOT_ALLOWED


class RequestTimeoutError(ServiceException):
    """HTTP 408 Request Timeout exception.

    Used when the server times out waiting for the request or processing takes too long.
    """

    status_code = status.HTTP_408_REQUEST_TIMEOUT
    message = "Request Timeout"
    code = ErrorCode.TIMEOUT


class ConflictError(ServiceException):
    """HTTP 409 Conflict exception.

    Used when the request conflicts with the current state (e.g., duplicate resource).
    """

    status_code = status.HTTP_409_CONFLICT
    message = "Conflict Error"
    code = ErrorCode.CONFLICT_ERROR


class ValidationError(ServiceException):
    """HTTP 422 Unprocessable Entity exception.

    Used when the request is syntactically correct but semantically invalid.
    Typically used for Pydantic validation errors.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    message = "Validation Error"
    code = ErrorCode.VALIDATION_ERROR


class RateLimitExceededError(ServiceException):
    """HTTP 429 Too Many Requests exception.

    Used when the client has exceeded their rate limit quota.
    """

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "Rate limit exceeded"
    code = ErrorCode.RATE_LIMIT_EXCEEDED


class InternalServerError(ServiceException):
    """HTTP 500 Internal Server Error exception.

    Used for unexpected server-side errors that are not the client's fault.
    """

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    message = "Internal Server Error"
    code = ErrorCode.INTERNAL_SERVER_ERROR


class BadGatewayError(ServiceException):
    """HTTP 502 Bad Gateway exception.

    Used when the server received an invalid response from an upstream server.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    message = "Bad Gateway"
    code = ErrorCode.BAD_GATEWAY


class ServiceUnavailableError(ServiceException):
    """HTTP 503 Service Unavailable exception.

    Used when the server is temporarily unable to handle requests (maintenance, overload).
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "Service Unavailable"
    code = ErrorCode.SERVICE_UNAVAILABLE
