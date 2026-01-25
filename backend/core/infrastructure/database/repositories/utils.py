"""Utility functions for repository layer operations."""

from collections.abc import Callable
from typing import TypeVar

from backend.core.infrastructure.database.repositories.base import PaginatedResult


TSource = TypeVar("TSource")
TDest = TypeVar("TDest")


def map_paginated_result(
    paginated_result: PaginatedResult[TSource],
    mapper: Callable[[TSource], TDest],
) -> PaginatedResult[TDest]:
    """Map items in a PaginatedResult while preserving all pagination metadata.

    This utility eliminates boilerplate when converting domain objects to DTOs
    in service methods that return paginated results. Instead of manually
    reconstructing a PaginatedResult with all 7 fields, this function handles
    the transformation automatically.

    Args:
        paginated_result: The source paginated result with items to map
        mapper: Function to transform each item (e.g., lambda x: Dto.from_domain(x))

    Returns:
        New PaginatedResult with transformed items and same metadata

    Example:
        # Instead of manually reconstructing with 7 fields:
        _paginated_result = await repo.search_events(filters)
        _dtos = [EventResponseDto.from_domain(e) for e in _paginated_result.items]
        return PaginatedResult(
            items=_dtos,
            current_page=_paginated_result.current_page,
            total_pages=_paginated_result.total_pages,
            total_results=_paginated_result.total_results,
            page_size=_paginated_result.page_size,
            has_next=_paginated_result.has_next,
            has_previous=_paginated_result.has_previous,
        )

        # Use this utility instead:
        return map_paginated_result(
            _paginated_result,
            mapper=EventResponseDto.from_domain,
        )
    """
    return PaginatedResult(
        items=[mapper(item) for item in paginated_result.items],
        current_page=paginated_result.current_page,
        total_pages=paginated_result.total_pages,
        total_results=paginated_result.total_results,
        page_size=paginated_result.page_size,
        has_next=paginated_result.has_next,
        has_previous=paginated_result.has_previous,
    )


__all__ = [
    "map_paginated_result",
]
