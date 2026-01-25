"""
Common DTO Classes for Frequently Used Patterns.

This module provides ready-to-use Pydantic DTOs for common API operations:
- ID-based operations (requests and responses)
- Success/acknowledgment responses
- Message responses
- Bulk operations
"""

from backend.core.api.dtos.base import BaseRequestDto, BaseResponseDto
from pydantic import Field


class IdRequestDto(BaseRequestDto):
    """
    Request DTO for operations requiring an ID.

    Use when an endpoint needs to receive an ID in the request body.

    Example:
        @router.post("/users/deactivate")
        async def deactivate_user(request: IdRequestDto):
            await user_service.deactivate(request.id)
    """

    id: str = Field(
        ...,
        description="Resource identifier (UUID format)",
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )


class IdResponseDto(BaseResponseDto):
    """
    Response DTO containing just an ID.

    Useful for create operations where you only need to return
    the created resource's ID.

    Example:
        @router.post("/users", response_model=ResponseModel[IdResponseDto])
        async def create_user(data: CreateUserDto) -> ResponseModel[IdResponseDto]:
            user_id = await user_service.create(data)
            return ResponseModel.ok(data=IdResponseDto(id=str(user_id)))
    """

    id: str = Field(
        ...,
        description="Created resource identifier",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )


class MessageResponseDto(BaseResponseDto):
    """
    Response DTO for simple message responses.

    Use for operations that don't return data but need to
    communicate a status message.

    Example:
        @router.post("/users/{user_id}/verify-email")
        async def verify_email(user_id: str) -> ResponseModel[MessageResponseDto]:
            await user_service.verify_email(user_id)
            return ResponseModel.ok(data=MessageResponseDto(message="Email verified successfully"))
    """

    message: str = Field(
        ...,
        description="Human-readable message",
        examples=["Operation completed successfully"],
    )


class SuccessResponseDto(BaseResponseDto):
    """
    Response DTO for simple success/failure acknowledgments.

    Example:
        @router.delete("/users/{user_id}")
        async def delete_user(user_id: str) -> ResponseModel[SuccessResponseDto]:
            await user_service.delete(user_id)
            return ResponseModel.ok(data=SuccessResponseDto(
                success=True,
                message="User deleted successfully"
            ))
    """

    success: bool = Field(
        ...,
        description="Whether the operation was successful",
        examples=[True, False],
    )
    message: str | None = Field(
        default=None,
        description="Optional message with details",
        examples=["Operation completed successfully"],
    )


class BulkIdRequestDto(BaseRequestDto):
    """
    Request DTO for bulk operations on multiple resources.

    Example:
        @router.post("/users/bulk-deactivate")
        async def bulk_deactivate(request: BulkIdRequestDto):
            await user_service.bulk_deactivate(request.ids)
    """

    ids: list[str] = Field(
        ...,
        description="List of resource identifiers",
        min_length=1,
        max_length=100,
        examples=[["id1", "id2", "id3"]],
    )


class BulkResultResponseDto(BaseResponseDto):
    """
    Response DTO for bulk operations results.

    Provides detailed feedback on which operations succeeded
    and which failed.

    Example:
        return ResponseModel.ok(data=BulkResultResponseDto(
            total=10,
            successful=8,
            failed=2,
            failed_ids=["id1", "id2"],
            errors={"id1": "Not found", "id2": "Permission denied"}
        ))
    """

    total: int = Field(
        ...,
        description="Total number of items processed",
        examples=[10],
    )
    successful: int = Field(
        ...,
        description="Number of successful operations",
        examples=[8],
    )
    failed: int = Field(
        ...,
        description="Number of failed operations",
        examples=[2],
    )
    failed_ids: list[str] = Field(
        default_factory=list,
        description="IDs of resources that failed",
        examples=[["id1", "id2"]],
    )
    errors: dict[str, str] | None = Field(
        default=None,
        description="Mapping of failed IDs to error messages",
        examples=[{"id1": "Not found", "id2": "Permission denied"}],
    )


class SlugRequestDto(BaseRequestDto):
    """
    Request DTO for operations requiring a slug identifier.

    Use for human-readable URL-friendly identifiers.
    """

    slug: str = Field(
        ...,
        description="URL-friendly identifier",
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        min_length=1,
        max_length=200,
        examples=["my-article-title"],
    )


class StatusResponseDto(BaseResponseDto):
    """
    Response DTO for status information.

    Useful for health checks and system status endpoints.
    """

    status: str = Field(
        ...,
        description="Current status",
        examples=["ok", "healthy", "degraded"],
    )
    service: str = Field(
        ...,
        description="Service name",
        examples=["api", "worker", "scheduler"],
    )
    version: str | None = Field(
        default=None,
        description="Service version",
        examples=["1.0.0"],
    )
