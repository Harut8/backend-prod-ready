from asyncio import current_task
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_scoped_session,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
import structlog

from backend.core.infrastructure.database.error_handler import database_error_handler
from backend.core.security.timeout import timeout


_SQL_LOG_MAX_LENGTH = 500


def _sanitize_statement(statement: str) -> str:
    """Sanitize SQL statement for logging - truncate long queries to prevent log bloat."""
    if len(statement) > _SQL_LOG_MAX_LENGTH:
        return statement[:_SQL_LOG_MAX_LENGTH] + "... [truncated]"
    return statement


def _convert_async_url_to_sync(async_url: str) -> str:
    """Convert async database URL to sync URL using proper URL parsing.

    Maps async drivers to their sync equivalents:
    - postgresql+asyncpg -> postgresql+psycopg2
    - postgresql+aiopg -> postgresql+psycopg2
    """
    _parsed = urlparse(async_url)
    _driver_map = {
        "postgresql+asyncpg": "postgresql+psycopg2",
        "postgresql+aiopg": "postgresql+psycopg2",
    }
    _new_scheme = _driver_map.get(_parsed.scheme, _parsed.scheme)
    return urlunparse(_parsed._replace(scheme=_new_scheme))


class PgAsyncSQLAlchemyAdapter:
    """PostgreSQL async adapter with connection pooling and timeout handling."""

    def __init__(self, url: str, *, echo: bool = False, logger: structlog.stdlib.BoundLogger | None = None) -> None:
        self._url = url
        self._echo = echo
        self._engine: AsyncEngine | None = None
        self._sync_engine: Engine | None = None
        self._sync_session_factory: sessionmaker[Session] | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._async_scoped_session: async_scoped_session[AsyncSession] | None = None
        self._logger = logger
        self.connect()

    @timeout(10.0)
    @database_error_handler
    async def dispose(self) -> None:
        if self._engine:
            await self._engine.dispose()
        if self._sync_engine:
            self._sync_engine.dispose()

    @database_error_handler
    def connect(self) -> None:
        self._engine = create_async_engine(
            url=self._url,
            echo=self._echo,
            pool_size=15,
            max_overflow=10,
            pool_timeout=2.0,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_use_lifo=True,
            connect_args={
                "timeout": 4.0,
                "command_timeout": 5.0,
                "server_settings": {
                    "statement_timeout": "5000",
                    "lock_timeout": "4000",
                },
            },
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        self._async_scoped_session = async_scoped_session(
            session_factory=self._session_factory,
            scopefunc=current_task,
        )

        # Create a separate synchronous engine for admin panel
        # Convert async URL (postgresql+asyncpg) to sync URL (postgresql+psycopg2)
        _sync_url = _convert_async_url_to_sync(self._url)
        self._sync_engine = create_engine(
            url=_sync_url,
            echo=self._echo,
            pool_size=5,  # Smaller pool for admin-only usage
            max_overflow=5,
            pool_timeout=2.0,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self._sync_session_factory = sessionmaker(
            bind=self._sync_engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

        self._setup_event_listeners()
        if self._logger:
            self._logger.info("Connected to Postgres database")

    def _setup_event_listeners(self) -> None:
        if self._engine is None:
            return

        @event.listens_for(self._engine.sync_engine, "before_cursor_execute")
        def before_cursor_execute(
            _conn: Any,
            _cursor: Any,
            _statement: Any,
            _parameters: Any,
            context: Any,
            _executemany: Any,
        ) -> None:
            context._query_start_time = time.perf_counter()  # noqa: SLF001

        @event.listens_for(self._engine.sync_engine, "after_cursor_execute")
        def after_cursor_execute(
            _conn: Any,
            _cursor: Any,
            statement: Any,
            _parameters: Any,
            context: Any,
            _executemany: Any,
        ) -> None:
            _total = time.perf_counter() - context._query_start_time  # noqa: SLF001
            if self._logger:
                _sanitized = _sanitize_statement(str(statement))
                self._logger.debug("SQL executed", duration=f"{_total:.5f}s", statement=_sanitized)

    @property
    def async_scoped_session(self) -> async_scoped_session[AsyncSession]:
        if self._async_scoped_session is None:
            msg = "Database connection not initialized"
            raise RuntimeError(msg)
        return self._async_scoped_session

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            msg = "Database engine not initialized"
            raise RuntimeError(msg)
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            msg = "Database session factory not initialized"
            raise RuntimeError(msg)
        return self._session_factory

    @property
    def sync_engine(self) -> Engine:
        if self._sync_engine is None:
            msg = "Sync database engine not initialized"
            raise RuntimeError(msg)
        return self._sync_engine

    @property
    def sync_session_factory(self) -> sessionmaker[Session]:
        if self._sync_session_factory is None:
            msg = "Sync database session factory not initialized"
            raise RuntimeError(msg)
        return self._sync_session_factory
