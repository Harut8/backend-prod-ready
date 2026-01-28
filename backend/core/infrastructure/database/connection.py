from asyncio import current_task
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_scoped_session,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
import structlog

from backend.core.conf.settings import SETTINGS
from backend.core.infrastructure.database.error_handler import database_error_handler
from backend.core.observability.metrics import update_db_pool_metrics
from backend.core.observability.tracing import instrument_sqlalchemy_engine
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
    """PostgreSQL async adapter with connection pooling and timeout handling.

    Uses lazy initialization pattern - engines are created on first access
    rather than during construction. This prevents blocking the event loop
    and allows proper startup health checks.
    """

    def __init__(self, url: str, *, echo: bool = False, logger: structlog.stdlib.BoundLogger | None = None) -> None:
        self._url = url
        self._echo = echo
        self._engine: AsyncEngine | None = None
        self._sync_engine: Engine | None = None
        self._sync_session_factory: sessionmaker[Session] | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._async_scoped_session: async_scoped_session[AsyncSession] | None = None
        self._logger = logger
        self._initialized = False

    @timeout(10.0)
    @database_error_handler
    async def dispose(self) -> None:
        if self._engine:
            await self._engine.dispose()
        if self._sync_engine:
            self._sync_engine.dispose()
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Ensure engines are initialized. Called lazily on first access."""
        if not self._initialized:
            self._connect_sync()

    @database_error_handler
    def _connect_sync(self) -> None:
        # NOTE: Database-level timeouts (statement_timeout, lock_timeout) are intentionally
        # NOT set here. Python-level timeouts via @timeout decorators are the single source
        # of truth. Setting both causes race conditions where:
        # 1. DB timeout fires first, leaving asyncpg connection in bad state
        # 2. Python timeout fires later, attempting cleanup on corrupted connection
        # This leads to connection pool corruption under heavy load.
        # See: backend/core/security/timeout.py for timeout configuration.
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
                "timeout": 4.0,  # Connection establishment timeout only
                "command_timeout": None,  # Let Python-level timeouts handle this
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

        # Instrument engine for OpenTelemetry tracing (must be done after engine creation)
        instrument_sqlalchemy_engine(self._engine)

        # Only enable SQL query timing listeners in non-production environments
        # to reduce tracing overhead (~13ms per query)
        if SETTINGS.APP.ENVIRONMENT != "prod":
            self._setup_event_listeners()
        self._initialized = True
        if self._logger:
            self._logger.info("Connected to Postgres database")

    async def initialize(self) -> None:
        """Explicitly initialize database connections during app startup.

        This method should be called during application startup to ensure
        database connections are established before handling requests.
        Allows for proper health checks and graceful failure handling.
        """
        if self._initialized:
            return

        self._connect_sync()

        # Verify async connection works
        if self._engine:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            if self._logger:
                self._logger.info("Database connection verified")

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
        self._ensure_initialized()
        if self._async_scoped_session is None:
            msg = "Database connection not initialized"
            raise RuntimeError(msg)
        return self._async_scoped_session

    @property
    def engine(self) -> AsyncEngine:
        self._ensure_initialized()
        if self._engine is None:
            msg = "Database engine not initialized"
            raise RuntimeError(msg)
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        self._ensure_initialized()
        if self._session_factory is None:
            msg = "Database session factory not initialized"
            raise RuntimeError(msg)
        return self._session_factory

    @property
    def sync_engine(self) -> Engine:
        self._ensure_initialized()
        if self._sync_engine is None:
            msg = "Sync database engine not initialized"
            raise RuntimeError(msg)
        return self._sync_engine

    @property
    def sync_session_factory(self) -> sessionmaker[Session]:
        self._ensure_initialized()
        if self._sync_session_factory is None:
            msg = "Sync database session factory not initialized"
            raise RuntimeError(msg)
        return self._sync_session_factory

    def record_pool_metrics(self) -> None:
        """Record database connection pool metrics to Prometheus.

        Extracts pool statistics from SQLAlchemy and updates the corresponding
        Prometheus gauges. Safe to call even if engine is not initialized.
        """
        if not self._initialized or self._engine is None:
            return

        try:
            pool = self._engine.pool
            # SQLAlchemy pool stats
            pool_size = pool.size()  # Configured pool size
            checked_in = pool.checkedin()  # Available connections
            checked_out = pool.checkedout()  # In-use connections

            update_db_pool_metrics(
                total=pool_size + pool.overflow(),
                available=checked_in,
                in_use=checked_out,
            )
        except Exception as e:
            if self._logger:
                self._logger.debug("Failed to record pool metrics", error=str(e))
