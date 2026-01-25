from abc import ABC, abstractmethod
import asyncio
import time
from typing import Any, Self, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from backend.core.conf.settings import SETTINGS
from backend.core.infrastructure.database.error_handler import database_error_handler
from backend.core.infrastructure.database.models import DbBaseModel
from backend.core.infrastructure.database.repositories import PgBaseRepository
from backend.core.infrastructure.database.session import SessionFactory
from backend.core.security.timeout import timeout_uow_aware


T = TypeVar("T", bound=DbBaseModel)

logger = structlog.get_logger(__name__)


class BaseUnitOfWork(ABC):
    """
    Unit of Work pattern for managing database transactions.

    Ensures operations execute within a single transaction with ACID properties.
    Supports both independent and orchestrated (shared session) transactions.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory: SessionFactory | None = None,
        *,
        auto_commit: bool = True,
    ) -> None:
        self._session: AsyncSession | None = session
        self._session_factory = session_factory
        self._repositories: dict[str, PgBaseRepository] = {}
        self._logger = logger.bind(uow=self.__class__.__name__)
        self._auto_commit = auto_commit
        self._owns_session = session is None
        self._timeout_occurred = False

    @property
    def owns_session(self) -> bool:
        return self._owns_session

    @property
    def auto_commit(self) -> bool:
        return self._auto_commit

    @auto_commit.setter
    def auto_commit(self, auto_commit: bool) -> None:
        self._auto_commit = auto_commit

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            msg = "Session not initialized"
            raise RuntimeError(msg)
        return self._session

    @session.setter
    def session(self, session: AsyncSession) -> None:
        self._session = session

    def configure_for_shared_session(self, session: AsyncSession) -> Self:
        """Configure UOW to use a shared session for orchestrated transactions."""
        self._session = session
        self._auto_commit = False
        self._owns_session = False
        return self

    @abstractmethod
    def _register_repositories(self) -> None:
        """Register feature-specific repositories."""
        ...

    @staticmethod
    def _raise_session_factory_error() -> None:
        """Raise error for missing session factory."""
        msg = "Session factory not provided"
        raise RuntimeError(msg)

    @timeout_uow_aware(7.0)
    @database_error_handler
    async def __aenter__(self) -> Self:
        """Enter async context and initialize session."""
        try:
            _start_time = time.perf_counter()
            if self._owns_session:
                if self._session_factory is None:
                    self._raise_session_factory_error()
                # mypy doesn't understand that _raise_session_factory_error raises
                assert self._session_factory is not None
                self._session = self._session_factory.create_session()
                self._logger.debug("Created own session")
            else:
                self._logger.debug("Using shared session")

            self._register_repositories()
        except Exception as e:
            self._logger.exception("Failed to initialize UOW", error=str(e))
            if self._session and self._owns_session:
                try:
                    await self._session.close()
                except Exception:
                    self._logger.exception("Failed to close session during cleanup")
            raise
        else:
            _duration = time.perf_counter() - _start_time
            self._logger.info("UOW initialized", duration=f"{_duration:.5f}s")
            return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit async context with automatic commit/rollback and cleanup."""
        _start_time = time.perf_counter()
        try:
            if not self._timeout_occurred and exc_type and exc_val:
                from backend.core.exceptions.db_exceptions import DatabaseTimeoutError  # noqa: PLC0415
                from backend.core.exceptions.http_exceptions import TimeoutException  # noqa: PLC0415

                if isinstance(exc_val, (TimeoutException, DatabaseTimeoutError, asyncio.TimeoutError)):
                    self._timeout_occurred = True
                    self._logger.info("Timeout detected", exception_type=exc_type.__name__)

            if exc_type:
                if self._owns_session:
                    await self.rollback()
            elif self._auto_commit:
                await self.commit()
        finally:
            if self._owns_session:
                await self._cleanup()
            else:
                self._repositories.clear()
        _duration = time.perf_counter() - _start_time
        self._logger.info("UOW cleaned up", duration=f"{_duration:.5f}s")

    async def _cleanup(self) -> None:
        """Cleanup session to prevent connection leaks."""
        if self._session:
            if self._timeout_occurred:
                self._logger.warning("Timeout occurred, applying asyncpg cleanup delay")
                await asyncio.sleep(SETTINGS.TIMEOUTS.ASYNCPG_CLEANUP_DELAY)

            try:
                await asyncio.wait_for(
                    asyncio.shield(self._session.close()),
                    timeout=SETTINGS.TIMEOUTS.SESSION_CLOSE_TIMEOUT,
                )
                self._logger.debug("Session closed successfully")
            except TimeoutError:
                self._logger.warning("Session close timed out, connection will be garbage collected")
                try:
                    if hasattr(self._session, "sync_session") and self._session.sync_session:
                        self._session.sync_session.close()
                        self._logger.debug("Forced sync session close")
                except (OSError, RuntimeError, AttributeError) as e:
                    self._logger.warning("Failed to force close sync session", error=str(e))
            except asyncio.CancelledError:
                self._logger.warning("Session cleanup was cancelled")
            except (OSError, RuntimeError) as e:
                self._logger.warning("Failed to close session", error=str(e))
            finally:
                self._session = None

        self._repositories.clear()

    @timeout_uow_aware(10.0)
    @database_error_handler
    async def commit(self) -> None:
        """Commit the current transaction."""
        if not self._session:
            msg = "Cannot commit without active session"
            raise RuntimeError(msg)

        try:
            await self._session.commit()
            self._logger.debug("Transaction committed")
        except Exception as e:
            self._logger.exception("Commit failed", error=str(e))
            await self.rollback()
            raise

    @database_error_handler
    async def rollback(self) -> None:
        """Rollback the current transaction."""
        if not self._session:
            msg = "Cannot rollback without active session"
            raise RuntimeError(msg)

        try:
            await self._session.rollback()
            self._logger.debug("Transaction rolled back")
        except Exception as e:
            self._logger.exception("Rollback failed", error=str(e))

    @timeout_uow_aware(10.0)
    @database_error_handler
    async def flush(self) -> None:
        """Flush changes to database without committing."""
        if not self._session:
            msg = "Cannot flush without active session"
            raise RuntimeError(msg)

        try:
            await self._session.flush()
            self._logger.debug("Session flushed")
        except Exception as e:
            self._logger.exception("Flush failed", error=str(e))
            raise

    def _add_repository(self, name: str, repository: PgBaseRepository[T]) -> None:
        """Register a repository instance for this UOW."""
        self._repositories[name] = repository
        setattr(self, name, repository)

    def __getattr__(self, name: str) -> Any:
        """Fallback attribute access for dynamic repository lookup."""
        if name in self._repositories:
            return self._repositories[name]
        msg = f"'{self.__class__.__name__}' has no attribute '{name}'"
        raise AttributeError(msg)
