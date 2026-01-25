"""
Base DTO Classes for Request and Response Objects.

This module provides the foundational Pydantic DTO classes that all API DTOs inherit from.
Uses Pydantic v2 with automatic camelCase conversion for JavaScript clients.

Features:
- Native FastAPI integration with automatic OpenAPI schema generation
- Automatic snake_case to camelCase conversion
- Immutable response models for thread safety
- Type-safe with full IDE support
"""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


T = TypeVar("T")


def to_camel(string: str) -> str:
    """
    Convert snake_case to camelCase.

    Examples:
        >>> to_camel("user_name")
        'userName'
        >>> to_camel("created_at")
        'createdAt'
        >>> to_camel("id")
        'id'
    """
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class BaseRequestDto(BaseModel):
    """
    Base class for all request DTOs.

    Features:
    - Automatic camelCase field aliasing for JavaScript clients
    - Allows population by field name or alias
    - Strict validation mode

    Usage:
        class CreateUserRequestDto(BaseRequestDto):
            email: str = Field(..., description="User email address")
            full_name: str = Field(..., min_length=1, max_length=100)

        # Accepts both:
        # {"email": "...", "fullName": "..."} (camelCase from client)
        # {"email": "...", "full_name": "..."} (snake_case)
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,  # Accept both snake_case and camelCase
        str_strip_whitespace=True,  # Strip whitespace from strings
        strict=False,  # Allow type coercion
        extra="forbid",  # Reject unknown fields
    )


class BaseResponseDto(BaseModel):
    """
    Base class for all response DTOs.

    Features:
    - Automatic camelCase field aliasing for JavaScript clients
    - Serialization by alias (outputs camelCase)
    - Immutable (frozen) for thread safety

    Usage:
        class UserResponseDto(BaseResponseDto):
            id: str = Field(..., description="Unique user identifier")
            email: str = Field(..., description="User email address")
            created_at: datetime = Field(..., description="Account creation timestamp")

        # Serializes to:
        # {"id": "...", "email": "...", "createdAt": "..."}
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,  # Accept both snake_case and camelCase
        from_attributes=True,  # Allow creation from ORM objects
        frozen=True,  # Immutable for thread safety
        ser_json_timedelta="iso8601",  # ISO format for timedelta
        ser_json_bytes="base64",  # Base64 for bytes
    )


class ResponseModel(BaseModel, Generic[T]):
    """
    Standard API response envelope.

    Provides a consistent structure for all API responses:
    {
        "success": true,
        "data": { ... },
        "meta": { ... }
    }

    Usage:
        @router.get("/users/{user_id}", response_model=ResponseModel[UserResponseDto])
        async def get_user(user_id: str) -> ResponseModel[UserResponseDto]:
            user = await user_service.get(user_id)
            return ResponseModel(data=UserResponseDto.model_validate(user))

        # Or use the factory function:
        return ResponseModel.ok(data=user_dto)

    Error response:
        return ResponseModel.error(message="User not found")
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    success: bool = Field(
        default=True,
        description="Indicates whether the request was successful",
    )

    data: T | None = Field(
        default=None,
        description="Response payload",
    )

    meta: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata (pagination, timing, etc.)",
    )

    message: str | None = Field(
        default=None,
        description="Optional message (typically for errors)",
    )

    @classmethod
    def ok(
        cls,
        data: T | None = None,
        meta: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> "ResponseModel[T]":
        """
        Create a successful response.

        Args:
            data: Response payload
            meta: Optional metadata
            message: Optional success message

        Returns:
            ResponseModel with success=True
        """
        return cls(success=True, data=data, meta=meta, message=message)

    @classmethod
    def error(
        cls,
        message: str,
        data: T | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "ResponseModel[T]":
        """
        Create an error response.

        Args:
            message: Error message
            data: Optional error details
            meta: Optional metadata

        Returns:
            ResponseModel with success=False
        """
        return cls(success=False, data=data, meta=meta, message=message)


class TimestampMixin(BaseModel):
    """
    Mixin providing standard timestamp fields.

    Use with multiple inheritance to add created_at and updated_at
    to your response DTOs.

    Usage:
        class UserResponseDto(BaseResponseDto, TimestampMixin):
            id: str
            name: str
            # created_at and updated_at are inherited
    """

    created_at: datetime = Field(
        ...,
        description="Resource creation timestamp (ISO 8601 format)",
    )
    updated_at: datetime = Field(
        ...,
        description="Resource last update timestamp (ISO 8601 format)",
    )
