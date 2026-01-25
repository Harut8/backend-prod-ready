"""
Base Data Transfer Objects (DTOs) for API Request/Response.

This module provides base Pydantic classes for all API DTOs following best practices:
- Pydantic v2 for validation and serialization
- Automatic camelCase conversion for JavaScript clients
- Standard response envelope pattern
- Pagination support
- Error response handling

Usage:
    from backend.core.api.dtos import BaseRequestDto, BaseResponseDto, ResponseModel

    class MyRequestDto(BaseRequestDto):
        name: str = Field(..., description="User name")

    class MyResponseDto(BaseResponseDto):
        id: str = Field(..., description="User ID")

    # In route handler:
    return ResponseModel(data=MyResponseDto(id="123"))
"""

from backend.core.api.dtos.base import (
    BaseRequestDto,
    BaseResponseDto,
    ResponseModel,
    to_camel,
)
from backend.core.api.dtos.common import (
    BulkIdRequestDto,
    BulkResultResponseDto,
    IdRequestDto,
    IdResponseDto,
    MessageResponseDto,
    SuccessResponseDto,
)
from backend.core.api.dtos.error import (
    ErrorDetail,
    ErrorResponse,
    FieldError,
    ValidationErrorDetail,
)
from backend.core.api.dtos.pagination import (
    CursorPaginatedResponse,
    CursorPaginationMeta,
    CursorPaginationParams,
    PaginatedResponse,
    PaginationMeta,
    PaginationParams,
    SortParams,
)


__all__ = [
    # Base DTOs
    "BaseRequestDto",
    "BaseResponseDto",
    "BulkIdRequestDto",
    "BulkResultResponseDto",
    "CursorPaginatedResponse",
    "CursorPaginationMeta",
    "CursorPaginationParams",
    # Error DTOs
    "ErrorDetail",
    "ErrorResponse",
    "FieldError",
    # Common DTOs
    "IdRequestDto",
    "IdResponseDto",
    "MessageResponseDto",
    # Pagination DTOs
    "PaginatedResponse",
    "PaginationMeta",
    "PaginationParams",
    "ResponseModel",
    "SortParams",
    "SuccessResponseDto",
    "ValidationErrorDetail",
    "to_camel",
]
