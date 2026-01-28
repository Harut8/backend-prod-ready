from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.infrastructure.database.connection import PgAsyncSQLAlchemyAdapter
from backend.core.infrastructure.database.error_handler import database_error_handler


class SessionFactory:
    """Factory for creating database sessions used by UOWs."""

    def __init__(self, adapter: PgAsyncSQLAlchemyAdapter) -> None:
        self._adapter = adapter

    @database_error_handler
    def create_session(self, session: AsyncSession | None = None) -> AsyncSession:
        """Create or return a database session."""
        if session is not None:
            return session
        return cast("AsyncSession", self._adapter.session_factory())

    def record_pool_metrics(self) -> None:
        """Record database connection pool metrics to Prometheus."""
        self._adapter.record_pool_metrics()
