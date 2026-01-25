"""Repository protocol interface for type-safe dependency injection.

This module defines the protocol (interface) for repository classes,
enabling database backend swapping and improved testability.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol, TypeVar

from sqlalchemy import Select


T = TypeVar("T")


class IRepository(Protocol[T]):
    """Protocol defining the standard repository interface.

    This protocol enables:
    - Type-safe dependency injection
    - Easy mocking in tests
    - Potential database backend swapping (e.g., PostgreSQL to SQLite for tests)

    All repository implementations should satisfy this protocol.
    """

    async def bool_check(self, stmt: Any) -> bool:
        """Check if a select statement returns any results."""
        ...

    async def select_one_orm(
        self,
        stmt: Any,
        *,
        unique: bool = False,
        refresh_attrs: list[str] | None = None,
    ) -> T | None:
        """Select single entity using ORM."""
        ...

    async def select_many_orm(
        self,
        stmt: Any,
        *,
        unique: bool = False,
    ) -> list[T]:
        """Select multiple entities using ORM."""
        ...

    async def select_one_for_update(
        self,
        stmt: Any,
        *,
        skip_locked: bool = False,
        nowait: bool = False,
        unique: bool = False,
        refresh_attrs: list[str] | None = None,
    ) -> T | None:
        """Select single entity with row-level lock."""
        ...

    async def select_many_for_update(
        self,
        stmt: Any,
        *,
        skip_locked: bool = False,
        nowait: bool = False,
        unique: bool = False,
    ) -> list[T]:
        """Select multiple entities with row-level locks."""
        ...

    async def insert_one_orm(
        self,
        instance: T,
        *,
        flush: bool = False,
        refresh_attrs: list[str] | None = None,
    ) -> T:
        """Insert single entity."""
        ...

    async def insert_many_orm(
        self,
        instances: Iterable[T] | Sequence[T],
        *,
        flush: bool = False,
    ) -> list[T]:
        """Insert multiple entities."""
        ...

    async def upsert_one_orm(
        self,
        instance: T,
        *,
        flush: bool = True,
        refresh_attrs: list[str] | None = None,
    ) -> T:
        """Upsert single entity using ORM merge()."""
        ...

    async def upsert_one_core(
        self,
        data: dict[str, Any],
        mapping: Any,
        conflict_fields: list[str],
        update_fields: list[str] | None = None,
        *,
        return_entity: bool = True,
        do_nothing_on_conflict: bool = False,
    ) -> T | None:
        """Upsert single row using INSERT...ON CONFLICT."""
        ...

    async def update_one_orm(
        self,
        entity: T,
        *,
        flush: bool = True,
        refresh_attrs: list[str] | None = None,
        **update_data: Any,
    ) -> T:
        """Update single entity."""
        ...

    async def update_many_orm(
        self,
        entities: list[T],
        payload: dict[str, Any],
        *,
        flush: bool = True,
    ) -> None:
        """Update multiple entities with same payload."""
        ...

    async def delete_one_orm(self, entity: T, *, flush: bool = False) -> None:
        """Delete single entity."""
        ...

    async def bulk_insert_orm(
        self,
        instances: Iterable[T],
        *,
        return_defaults: bool = True,
    ) -> list[T]:
        """Bulk insert using ORM bulk_save_objects."""
        ...

    async def bulk_insert_core(
        self,
        instances: Iterable[dict[str, Any]],
        mapping: Any,
    ) -> list[T]:
        """Bulk insert using Core SQL."""
        ...

    async def bulk_upsert_core(
        self,
        instances: list[dict[str, Any]],
        mapping: Any,
        on_conflict: Callable[[Any], Any],
    ) -> list[Any]:
        """Bulk upsert using INSERT...ON CONFLICT with RETURNING."""
        ...


class IPaginatedRepository(IRepository[T], Protocol):
    """Extended protocol for repositories with pagination support."""

    async def paginated_select_with_metadata(
        self,
        stmt: Select[Any],
        page_size: int,
        page_number: int,
    ) -> Any:
        """Execute paginated query with complete metadata."""
        ...

    async def paginated_select_fast(
        self,
        stmt: Select[Any],
        page_size: int,
        page_number: int,
    ) -> Any:
        """Execute paginated query WITHOUT count - faster for high-throughput."""
        ...


class IDomainRepository(IRepository[T], Protocol):
    """Extended protocol for repositories with domain conversion support."""

    async def select_as_domain(
        self,
        stmt: Any,
        *,
        one: bool = True,
        unique: bool = False,
    ) -> Any:
        """Select and auto-convert to domain model(s)."""
        ...

    async def update_from_domain(
        self,
        domain: Any,
        *,
        flush: bool = True,
        refresh_attrs: list[str] | None = None,
    ) -> T | None:
        """Update entity from domain object."""
        ...

    async def execute_returning_as_domain(
        self,
        stmt: Any,
        refresh_attrs: list[str] | None = None,
    ) -> Any:
        """Execute UPDATE/DELETE with RETURNING clause and convert to domain."""
        ...
