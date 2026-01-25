"""
Error Response DTO Classes.

This module provides Pydantic DTOs for standardized error responses:
- Generic error details
- Validation error details
- Field-level errors

These DTOs ensure consistent error response structure across all endpoints.
"""

from datetime import datetime
from typing import Any

from backend.core.api.dtos.base import BaseResponseDto
from pydantic import Field


class ErrorDetail(BaseResponseDto):
    """
    Standard error detail structure.

    Used within ErrorResponse to provide error information.

    Example:
        {
            "code": "NOT_FOUND",
            "message": "User not found",
            "target": "user_id"
        }
    """

    code: str = Field(
        ...,
        description="Machine-readable error code",
        examples=["NOT_FOUND", "VALIDATION_ERROR", "UNAUTHORIZED"],
    )

    message: str = Field(
        ...,
        description="Human-readable error message",
        examples=["User not found", "Invalid email format"],
    )

    target: str | None = Field(
        default=None,
        description="The target of the error (e.g., field name)",
        examples=["user_id", "email", "password"],
    )

    details: dict[str, Any] | None = Field(
        default=None,
        description="Additional error details",
        examples=[{"min_length": 8, "actual_length": 5}],
    )


class FieldError(BaseResponseDto):
    """
    Field-level validation error.

    Used to report validation errors on specific fields.
    """

    field: str = Field(
        ...,
        description="Field name that has the error",
        examples=["email", "password", "username"],
    )

    message: str = Field(
        ...,
        description="Error message for this field",
        examples=["This field is required", "Invalid email format"],
    )

    code: str = Field(
        default="validation_error",
        description="Error code for this field",
        examples=["required", "invalid_format", "too_short"],
    )

    value: Any = Field(
        default=None,
        description="The invalid value that was provided (for debugging)",
    )


class ValidationErrorDetail(BaseResponseDto):
    """
    Validation error response with field-level details.

    Used when request validation fails to provide detailed
    feedback on which fields have issues.

    Example:
        {
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "errors": [
                {"field": "email", "message": "Invalid email format", "code": "invalid_format"},
                {"field": "password", "message": "Must be at least 8 characters", "code": "too_short"}
            ]
        }
    """

    code: str = Field(
        default="VALIDATION_ERROR",
        description="Error code",
    )

    message: str = Field(
        default="Request validation failed",
        description="General error message",
    )

    errors: list[FieldError] = Field(
        ...,
        description="List of field-level validation errors",
    )


class ErrorResponse(BaseResponseDto):
    """
    Standard error response structure.

    All error responses should follow this format for consistency.

    Example response body:
        {
            "success": false,
            "error": {
                "code": "NOT_FOUND",
                "message": "User with id 'abc' not found",
                "target": "user_id"
            },
            "timestamp": "2024-01-15T10:30:00Z",
            "requestId": "abc-123-def"
        }
    """

    success: bool = Field(
        default=False,
        description="Always false for error responses",
    )

    error: ErrorDetail = Field(
        ...,
        description="Error details",
    )

    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the error occurred",
    )

    request_id: str | None = Field(
        default=None,
        description="Request ID for tracking/debugging",
        examples=["abc-123-def"],
    )

    path: str | None = Field(
        default=None,
        description="Request path that caused the error",
        examples=["/api/v1/users/123"],
    )

    @classmethod
    def create(
        cls,
        code: str,
        message: str,
        *,
        target: str | None = None,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
        path: str | None = None,
    ) -> "ErrorResponse":
        """
        Factory method to create an error response.

        Args:
            code: Machine-readable error code
            message: Human-readable error message
            target: The target of the error (optional)
            details: Additional error details (optional)
            request_id: Request ID for tracking (optional)
            path: Request path (optional)

        Returns:
            ErrorResponse instance
        """
        return cls(
            error=ErrorDetail(
                code=code,
                message=message,
                target=target,
                details=details,
            ),
            request_id=request_id,
            path=path,
        )

    @classmethod
    def validation_error(
        cls,
        errors: list[FieldError],
        *,
        message: str = "Request validation failed",
        request_id: str | None = None,
        path: str | None = None,
    ) -> "ErrorResponse":
        """
        Factory method to create a validation error response.

        Args:
            errors: List of field-level errors
            message: General error message
            request_id: Request ID for tracking (optional)
            path: Request path (optional)

        Returns:
            ErrorResponse with validation details
        """
        return cls(
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                message=message,
                details={"errors": [e.model_dump() for e in errors]},
            ),
            request_id=request_id,
            path=path,
        )

    @classmethod
    def not_found(
        cls,
        resource: str,
        identifier: str | None = None,
        *,
        request_id: str | None = None,
        path: str | None = None,
    ) -> "ErrorResponse":
        """
        Factory method for not found errors.

        Args:
            resource: Type of resource not found
            identifier: Resource identifier (optional)
            request_id: Request ID for tracking (optional)
            path: Request path (optional)

        Returns:
            ErrorResponse for not found
        """
        message = f"{resource} not found"
        if identifier:
            message = f"{resource} with id '{identifier}' not found"

        return cls.create(
            code="NOT_FOUND",
            message=message,
            target=resource.lower(),
            request_id=request_id,
            path=path,
        )

    @classmethod
    def unauthorized(
        cls,
        message: str = "Authentication required",
        *,
        request_id: str | None = None,
        path: str | None = None,
    ) -> "ErrorResponse":
        """Factory method for unauthorized errors."""
        return cls.create(
            code="UNAUTHORIZED",
            message=message,
            request_id=request_id,
            path=path,
        )

    @classmethod
    def forbidden(
        cls,
        message: str = "Access denied",
        *,
        request_id: str | None = None,
        path: str | None = None,
    ) -> "ErrorResponse":
        """Factory method for forbidden errors."""
        return cls.create(
            code="FORBIDDEN",
            message=message,
            request_id=request_id,
            path=path,
        )

    @classmethod
    def internal_error(
        cls,
        message: str = "An unexpected error occurred",
        *,
        request_id: str | None = None,
        path: str | None = None,
    ) -> "ErrorResponse":
        """Factory method for internal server errors."""
        return cls.create(
            code="INTERNAL_ERROR",
            message=message,
            request_id=request_id,
            path=path,
        )
