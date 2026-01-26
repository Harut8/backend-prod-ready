from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, Self, TypeVar, cast

from sqlalchemy import Row, Select, inspect as sa_inspect, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from backend.core.conf.settings import SETTINGS
from backend.core.infrastructure.database.error_handler import database_error_handler
from backend.core.infrastructure.database.models import DbBaseModel
from backend.core.security.circuit_breaker import (
    get_db_circuit_breaker_factory,
    type_preserving_circuit_breaker,
)
from backend.core.security.timeout import timeout


if TYPE_CHECKING:
    from backend.core.infrastructure.database.models import DbBaseModel

# It must be a subclass of DbBaseModel[DomainT] where DomainT is any domain type
T = TypeVar("T", bound="DbBaseModel[Any]")


@dataclass
class PaginatedResult(Generic[T]):
    """Pagination result for repository layer."""

    items: list[T]
    current_page: int
    total_pages: int
    total_results: int
    page_size: int
    has_next: bool
    has_previous: bool


class SelectMode(Enum):
    """Execution modes for select statements."""

    ONE = "one"
    ALL = "all"
    UNIQUE_ENTITY = "unique_entity"
    DICT = "dict"
    UNIQUE_DICT = "unique_dict"
    ALL_DICT = "all_dict"
    ROW = "row"
    SCALAR_LIST = "scalar_list"


_db_circuit_breaker = type_preserving_circuit_breaker(get_db_circuit_breaker_factory()("db_operations"))


def apply_repository_decorators(func: Callable[..., Any]) -> Callable[..., Any]:
    """Apply standard repository decorators: circuit breaker -> timeout -> error handler.

    Note: This function is used for runtime decorator application. For new code,
    prefer using the @repository_method decorator directly on methods.
    """
    return database_error_handler(timeout(SETTINGS.TIMEOUTS.REPOSITORY_TIMEOUT)(_db_circuit_breaker(func)))  # type: ignore[no-any-return]


def repository_method(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that applies standard repository decorators at definition time.

    This is the preferred way to decorate repository methods. It applies:
    - Circuit breaker (fail-fast when DB is unhealthy)
    - Timeout (prevent long-running queries)
    - Error handler (convert exceptions to domain errors)

    Usage:
        @repository_method
        async def get_user(self, user_id: int) -> User | None:
            stmt = select(User).where(User.id == user_id)
            result = await self.session.execute(stmt)
            return result.scalar()
    """
    return apply_repository_decorators(func)


class PgBaseRepository(Generic[T]):
    """Base repository for PostgreSQL with standard CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @classmethod
    def factory(cls: type[Self], session: AsyncSession, **kwargs: Any) -> Self:
        """Factory method to create repository instances."""
        return cls(session, **kwargs)

    @property
    def session(self) -> AsyncSession:
        return self._session

    @session.setter
    def session(self, session: AsyncSession) -> None:
        self._session = session

    def _get_model_class(self) -> type[T]:
        """
        Get the model class from generic type parameter.

        This extracts the model class from the repository's generic type inheritance.
        For example, PreRegistrationRepository(PgBaseRepository[PreRegistration])
        will return PreRegistration.

        """
        try:
            return self.__orig_bases__[0].__args__[0]  # type: ignore[attr-defined, no-any-return]
        except (AttributeError, IndexError) as e:
            msg = (
                f"{self.__class__.__name__} must properly inherit from "
                f"PgBaseRepository[ModelClass] with an explicit model type"
            )
            raise RuntimeError(msg) from e

    @staticmethod
    def as_dict(row: Row[Any]) -> dict[str, Any]:
        return row._asdict() if row else {}

    async def _execute_select(  # noqa: PLR0911
        self,
        stmt: Any,
        mode: SelectMode = SelectMode.ONE,
    ) -> T | list[T] | dict[str, Any] | list[dict[str, Any]] | list[Row[Any]] | list[Any] | None:
        """Execute select statement with specified mode."""
        result = await self.session.execute(stmt)

        match mode:
            case SelectMode.ONE:
                return result.scalar()  # type: ignore[no-any-return]
            case SelectMode.ALL:
                return result.scalars().all()  # type: ignore[no-any-return]
            case SelectMode.UNIQUE_ENTITY:
                rows = result.unique().all()
                return [row[0] for row in rows]
            case SelectMode.DICT:
                row = result.first()
                return PgBaseRepository.as_dict(row)
            case SelectMode.UNIQUE_DICT:
                rows = result.unique().all()
                return [PgBaseRepository.as_dict(row) for row in rows]
            case SelectMode.ALL_DICT:
                rows = result.all()
                return [PgBaseRepository.as_dict(row) for row in rows]
            case SelectMode.ROW:
                return result.all()  # type: ignore[no-any-return]
            case SelectMode.SCALAR_LIST:
                return [row[0] for row in result.all()]

    @repository_method
    async def bool_check(self, stmt: Any) -> bool:
        """Check if a select statement returns any results."""
        result = await self.session.execute(stmt)
        return result.scalar() is not None

    @repository_method
    async def select_one_orm(
        self,
        stmt: Any,
        *,
        unique: bool = False,
        refresh_attrs: list[str] | None = None,
    ) -> T | None:
        """Select single entity using ORM."""
        return await self._select_one_orm_internal(stmt, unique=unique, refresh_attrs=refresh_attrs)

    async def _select_one_orm_internal(
        self,
        stmt: Any,
        *,
        unique: bool = False,
        refresh_attrs: list[str] | None = None,
    ) -> T | None:
        """Internal implementation for select_one_orm (no decorators)."""
        entity: T | None
        if unique:
            result = await self._execute_select(stmt, SelectMode.UNIQUE_ENTITY)
            result_list = cast("list[T]", result)
            entity = result_list[0] if result_list else None
        else:
            entity = cast("T | None", await self._execute_select(stmt, SelectMode.ONE))

        if entity and refresh_attrs:
            await self.session.refresh(entity, refresh_attrs)

        return entity

    @repository_method
    async def select_many_orm(
        self,
        stmt: Any,
        *,
        unique: bool = False,
    ) -> list[T]:
        """Select multiple entities using ORM."""
        return await self._select_many_orm_internal(stmt, unique=unique)

    async def _select_many_orm_internal(
        self,
        stmt: Any,
        *,
        unique: bool = False,
    ) -> list[T]:
        """Internal implementation for select_many_orm (no decorators)."""
        if unique:
            return cast("list[T]", await self._execute_select(stmt, SelectMode.UNIQUE_ENTITY))
        return cast("list[T]", await self._execute_select(stmt, SelectMode.ALL))

    @repository_method
    async def select_one_for_update(
        self,
        stmt: Any,
        *,
        skip_locked: bool = False,
        nowait: bool = False,
        unique: bool = False,
        refresh_attrs: list[str] | None = None,
    ) -> T | None:
        """Select single entity with row-level lock for safe read-modify-write operations.

        Use this for state transitions (payments, subscriptions) to prevent lost updates.

        Args:
            stmt: SQLAlchemy select statement
            skip_locked: Skip rows locked by other transactions (useful for job queues)
            nowait: Raise exception immediately if lock cannot be acquired
            unique: Apply unique() to result for joined relationships
            refresh_attrs: Attributes to refresh after selection
        """
        _locked_stmt = stmt.with_for_update(skip_locked=skip_locked, nowait=nowait)
        return await self._select_one_orm_internal(_locked_stmt, unique=unique, refresh_attrs=refresh_attrs)

    @repository_method
    async def select_many_for_update(
        self,
        stmt: Any,
        *,
        skip_locked: bool = False,
        nowait: bool = False,
        unique: bool = False,
    ) -> list[T]:
        """Select multiple entities with row-level locks for batch operations.

        Use this when updating multiple related records atomically.

        Args:
            stmt: SQLAlchemy select statement
            skip_locked: Skip rows locked by other transactions
            nowait: Raise exception immediately if lock cannot be acquired
            unique: Apply unique() to result for joined relationships
        """
        _locked_stmt = stmt.with_for_update(skip_locked=skip_locked, nowait=nowait)
        return await self._select_many_orm_internal(_locked_stmt, unique=unique)

    @repository_method
    async def select_scalar_list(self, stmt: Any) -> list[Any]:
        """Execute statement and return list of scalar values from first column."""
        return cast("list[Any]", await self._execute_select(stmt, SelectMode.SCALAR_LIST))

    @repository_method
    async def paginated_select_with_metadata(
        self, stmt: Select[Any], page_size: int, page_number: int
    ) -> PaginatedResult[T]:
        """Execute paginated query with complete metadata."""
        # Optimize count query by removing ordering (not needed for counting)
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        count_result = await self.session.execute(count_stmt)
        total_results = count_result.scalar() or 0

        total_pages = (total_results + page_size - 1) // page_size if total_results > 0 else 1
        has_next = page_number < total_pages
        has_previous = page_number > 1

        _offset = (page_number - 1) * page_size
        _limit = page_size
        paginated_stmt = stmt.limit(_limit).offset(_offset)
        items = cast("list[T]", await self._execute_select(paginated_stmt, SelectMode.ALL))

        return PaginatedResult(
            items=items,
            current_page=page_number,
            total_pages=total_pages,
            total_results=total_results,
            page_size=page_size,
            has_next=has_next,
            has_previous=has_previous,
        )

    @repository_method
    async def paginated_select_fast(self, stmt: Select[Any], page_size: int, page_number: int) -> PaginatedResult[T]:
        """Execute paginated query WITHOUT count - much faster for high-throughput scenarios.

        Returns approximate pagination metadata by fetching page_size + 1 items.
        If we get more than page_size items, we know there's a next page.
        """
        # Fetch one extra item to check if there's a next page
        _offset = (page_number - 1) * page_size
        _limit = page_size + 1
        paginated_stmt = stmt.limit(_limit).offset(_offset)
        items = cast("list[T]", await self._execute_select(paginated_stmt, SelectMode.ALL))

        # Check if we have more items than requested
        has_next = len(items) > page_size
        if has_next:
            items = items[:page_size]  # Remove the extra item

        has_previous = page_number > 1

        return PaginatedResult(
            items=items,
            current_page=page_number,
            total_pages=-1,  # Unknown without count
            total_results=-1,  # Unknown without count
            page_size=page_size,
            has_next=has_next,
            has_previous=has_previous,
        )

    @repository_method
    async def insert_one_orm(
        self,
        instance: T,
        *,
        flush: bool = False,
        refresh_attrs: list[str] | None = None,
    ) -> T:
        """Insert single entity."""
        self.session.add(instance)

        if flush:
            await self.session.flush()
            if refresh_attrs:
                await self.session.refresh(instance, refresh_attrs)

        return instance

    @repository_method
    async def upsert_one_orm(
        self,
        instance: T,
        *,
        flush: bool = True,
        refresh_attrs: list[str] | None = None,
    ) -> T:
        """Upsert single entity using ORM merge()."""
        merged_instance: T = await self.session.merge(instance)

        if flush:
            await self.session.flush()
            if refresh_attrs:
                await self.session.refresh(merged_instance, refresh_attrs)

        return merged_instance

    @repository_method
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
        stmt = insert(mapping).values(**data)

        if do_nothing_on_conflict or (update_fields is not None and len(update_fields) == 0):
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_fields)
        else:
            if update_fields is None:
                update_dict = {k: v for k, v in data.items() if k not in conflict_fields}
            else:
                update_dict = {k: stmt.excluded[k] for k in update_fields if k in data}

            stmt = stmt.on_conflict_do_update(index_elements=conflict_fields, set_=update_dict)

        if return_entity:
            stmt_with_returning = stmt.returning(mapping)
            result = await self.session.execute(stmt_with_returning)
            return cast("T | None", result.scalar_one_or_none())

        await self.session.execute(stmt)
        return None

    @repository_method
    async def insert_many_orm(
        self,
        instances: Iterable[T] | Sequence[T],
        *,
        flush: bool = False,
    ) -> list[T]:
        """Insert multiple entities."""
        self.session.add_all(instances)

        if flush:
            await self.session.flush()

        return list(instances)

    @repository_method
    async def bulk_insert_orm(self, instances: Iterable[T], *, return_defaults: bool = True) -> list[T]:
        """Bulk insert using ORM bulk_save_objects."""
        await self.session.run_sync(lambda ses: ses.bulk_save_objects(instances, return_defaults=return_defaults))
        return list(instances)

    @repository_method
    async def bulk_insert_core(self, instances: Iterable[dict[str, Any]], mapping: Any) -> list[T]:
        """Bulk insert using Core SQL."""
        await self.session.run_sync(lambda ses: ses.bulk_insert_mappings(mapping, instances))
        return cast("list[T]", instances)

    @repository_method
    async def bulk_upsert_core(
        self, instances: list[dict[str, Any]], mapping: Any, on_conflict: Callable[[Any], Any]
    ) -> list[Any]:
        """Bulk upsert using INSERT...ON CONFLICT with RETURNING."""
        stmt = insert(mapping).values(instances)
        stmt = on_conflict(stmt)
        stmt = stmt.returning(mapping)
        _res = await self.session.execute(stmt)
        return cast("list[Row[Any]]", _res.scalars().all())

    @repository_method
    async def update_one_orm(
        self,
        entity: T,
        *,
        flush: bool = True,
        refresh_attrs: list[str] | None = None,
        **update_data: Any,
    ) -> T:
        """Update single entity."""
        return await self._update_one_orm_internal(entity, flush=flush, refresh_attrs=refresh_attrs, **update_data)

    async def _update_one_orm_internal(
        self,
        entity: T,
        *,
        flush: bool = True,
        refresh_attrs: list[str] | None = None,
        **update_data: Any,
    ) -> T:
        """Internal implementation for update_one_orm (no decorators)."""
        mapper = sa_inspect(entity.__class__)
        column_names = {col.key for col in mapper.columns}

        for field, value in update_data.items():
            if field in column_names and value is not None:
                setattr(entity, field, value)

        self.session.add(entity)

        if flush:
            await self.session.flush()
            if refresh_attrs:
                await self.session.refresh(entity, refresh_attrs)

        return entity

    @repository_method
    async def update_many_orm(
        self,
        entities: list[T],
        payload: dict[str, Any],
        *,
        flush: bool = True,
    ) -> None:
        """Update multiple entities with same payload."""
        for entity in entities:
            for field, value in payload.items():
                if hasattr(entity, field):
                    setattr(entity, field, value)
            self.session.add(entity)

        if flush:
            await self.session.flush()

    @repository_method
    async def run_update_stmt(self, stmt: Any) -> None:
        """Execute Core SQL update."""
        await self.session.execute(stmt)

    @repository_method
    async def delete_one_orm(self, entity: T, *, flush: bool = False) -> None:
        """Delete single entity."""
        await self.session.delete(entity)

        if flush:
            await self.session.flush()

    @repository_method
    async def run_delete_stmt(self, stmt: Any) -> Any:
        """Execute Core SQL delete."""
        return await self.session.execute(stmt)

    @repository_method
    async def select_as_domain(
        self,
        stmt: Any,
        *,
        one: bool = True,
        unique: bool = False,
    ) -> Any:
        """Select and auto-convert to domain model(s)."""
        if one:
            entity = await self._select_one_orm_internal(stmt, unique=unique)
            return entity.to_domain() if entity else None
        entities = await self._select_many_orm_internal(stmt, unique=unique)
        return [entity.to_domain() for entity in entities]

    @repository_method
    async def execute_returning_as_domain(
        self,
        stmt: Any,
        refresh_attrs: list[str] | None = None,
    ) -> Any:
        """Execute UPDATE/DELETE statement with RETURNING clause and convert to domain."""
        result = await self.session.execute(stmt)
        entity = result.scalar_one_or_none()

        if entity and refresh_attrs:
            await self.session.refresh(entity, refresh_attrs)

        return entity.to_domain() if entity else None

    @repository_method
    async def update_from_domain(
        self,
        domain: Any,
        *,
        flush: bool = True,
        refresh_attrs: list[str] | None = None,
    ) -> T | None:
        """Update entity from domain object."""
        _model_class = self._get_model_class()

        # Use SELECT ... FOR UPDATE to prevent lost updates in concurrent operations
        # This acquires a row-level lock that blocks other transactions until commit/rollback
        _stmt = select(_model_class).where(_model_class.id == domain.id).with_for_update()
        _existing_entity = await self._select_one_orm_internal(_stmt)

        if not _existing_entity:
            return None

        _updated_values = _model_class.from_domain(domain)

        _mapper = sa_inspect(_model_class)
        _update_data = {}
        for _col in _mapper.columns:
            _field = _col.key
            if _field in ("id", "created_at"):
                continue
            if _field in _updated_values.__dict__:
                _value = _updated_values.__dict__[_field]
                if _value is not None:
                    _update_data[_field] = _value

        return await self._update_one_orm_internal(
            _existing_entity,
            flush=flush,
            refresh_attrs=refresh_attrs,
            **_update_data,
        )
