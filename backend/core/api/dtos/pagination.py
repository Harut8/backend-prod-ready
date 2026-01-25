"""
Pagination DTO Classes.

This module provides Pydantic DTOs for implementing pagination:
- Offset-based pagination (traditional page/limit)
- Cursor-based pagination (for real-time data and large datasets)
- Pagination metadata

Best Practices:
- Use offset pagination for small-medium datasets with known size
- Use cursor pagination for large datasets, real-time data, or infinite scroll
- Always include total count when using offset pagination
"""

from typing import Generic, TypeVar

from backend.core.api.dtos.base import BaseRequestDto, BaseResponseDto
from pydantic import Field, computed_field


T = TypeVar("T")


class PaginationParams(BaseRequestDto):
    """
    Parameters for offset-based pagination.

    Use for traditional page-based navigation.

    Example:
        @router.get("/users")
        async def list_users(
            page: int = Query(1, ge=1),
            per_page: int = Query(20, ge=1, le=100),
        ):
            pagination = PaginationParams(page=page, per_page=per_page)
            users, total = await user_service.list(
                offset=pagination.offset,
                limit=pagination.limit,
            )
    """

    page: int = Field(
        default=1,
        ge=1,
        description="Page number (1-indexed)",
        examples=[1],
    )

    per_page: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of items per page",
        examples=[20],
    )

    @property
    @computed_field
    def offset(self) -> int:
        """Calculate the database offset for this page."""
        return (self.page - 1) * self.per_page

    @property
    @computed_field
    def limit(self) -> int:
        """Get the limit (alias for per_page)."""
        return self.per_page


class PaginationMeta(BaseResponseDto):
    """
    Pagination metadata for offset-based pagination.

    Provides all information needed for building pagination UI.
    """

    page: int = Field(
        ...,
        description="Current page number",
        examples=[1],
    )

    per_page: int = Field(
        ...,
        description="Number of items per page",
        examples=[20],
    )

    total: int = Field(
        ...,
        description="Total number of items across all pages",
        examples=[100],
    )

    total_pages: int = Field(
        ...,
        description="Total number of pages",
        examples=[5],
    )

    has_next: bool = Field(
        default=True,
        description="Whether there is a next page",
        examples=[True],
    )

    has_prev: bool = Field(
        default=False,
        description="Whether there is a previous page",
        examples=[False],
    )

    @classmethod
    def create(cls, page: int, per_page: int, total: int) -> "PaginationMeta":
        """
        Factory method to create pagination meta with computed fields.

        Args:
            page: Current page number (1-indexed)
            per_page: Items per page
            total: Total number of items

        Returns:
            PaginationMeta with all computed fields
        """
        total_pages = (total + per_page - 1) // per_page if per_page > 0 else 0
        return cls(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        )


class PaginatedResponse(BaseResponseDto, Generic[T]):
    """
    Generic paginated response DTO.

    Wraps a list of items with pagination metadata.

    Example:
        @router.get("/users", response_model=ResponseModel[PaginatedResponse[UserResponseDto]])
        async def list_users(page: int = 1, per_page: int = 20):
            users, total = await user_service.list(page, per_page)
            return ResponseModel.ok(data=PaginatedResponse(
                items=[UserResponseDto.model_validate(u) for u in users],
                pagination=PaginationMeta.create(page=page, per_page=per_page, total=total)
            ))
    """

    items: list[T] = Field(
        ...,
        description="List of items for the current page",
    )

    pagination: PaginationMeta = Field(
        ...,
        description="Pagination metadata",
    )


class CursorPaginationParams(BaseRequestDto):
    """
    Parameters for cursor-based pagination.

    Use for real-time data, large datasets, or infinite scroll UIs.

    Advantages over offset pagination:
    - Consistent results when data changes between pages
    - Better performance for large offsets
    - Works well with real-time data
    """

    cursor: str | None = Field(
        default=None,
        description="Opaque cursor for pagination (null for first page)",
        examples=["eyJpZCI6MTAwfQ=="],
    )

    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of items to return",
        examples=[20],
    )


class CursorPaginationMeta(BaseResponseDto):
    """
    Pagination metadata for cursor-based pagination.
    """

    next_cursor: str | None = Field(
        ...,
        description="Cursor for fetching the next page (null if no more pages)",
        examples=["eyJpZCI6MTAwfQ=="],
    )

    has_more: bool = Field(
        ...,
        description="Whether there are more items to fetch",
        examples=[True],
    )

    prev_cursor: str | None = Field(
        default=None,
        description="Cursor for fetching the previous page (null if first page)",
        examples=["eyJpZCI6NTB9"],
    )


class CursorPaginatedResponse(BaseResponseDto, Generic[T]):
    """
    Generic cursor-paginated response DTO.

    Example:
        return CursorPaginatedResponse(
            items=articles,
            pagination=CursorPaginationMeta(
                next_cursor=next_cursor,
                has_more=len(articles) == limit,
            )
        )
    """

    items: list[T] = Field(
        ...,
        description="List of items",
    )

    pagination: CursorPaginationMeta = Field(
        ...,
        description="Cursor pagination metadata",
    )


class SortParams(BaseRequestDto):
    """
    Parameters for sorting.

    Use with pagination to allow clients to specify sort order.
    """

    sort_by: str = Field(
        default="created_at",
        description="Field to sort by",
        examples=["created_at", "name", "updated_at"],
    )

    sort_order: str = Field(
        default="desc",
        pattern=r"^(asc|desc)$",
        description="Sort order (asc or desc)",
        examples=["desc", "asc"],
    )

    @property
    @computed_field
    def is_ascending(self) -> bool:
        """Check if sort order is ascending."""
        return self.sort_order.lower() == "asc"
