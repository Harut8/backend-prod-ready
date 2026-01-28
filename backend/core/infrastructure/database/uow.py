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

    IMPORTANT - Shared Session Usage:
        When using shared sessions (via configure_for_shared_session), be aware that
        SQLAlchemy AsyncSession is NOT safe for concurrent access from multiple coroutines.
        If you need to use multiple UoWs with a shared session concurrently, you MUST:
        1. Pass a shared lock via configure_for_shared_session(session, shared_lock=lock)
        2. Use the lock to coordinate access to the session

        For truly concurrent operations, prefer creating separate UoWs with their own sessions.
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
        # Lock for coordinating shared session access across concurrent UoWs
        self._shared_session_lock: asyncio.Lock | None = None

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

    def configure_for_shared_session(
        self,
        session: AsyncSession,
        shared_lock: asyncio.Lock | None = None,
    ) -> Self:
        """Configure UOW to use a shared session for orchestrated transactions.

        Args:
            session: The shared AsyncSession to use
            shared_lock: Optional lock for coordinating concurrent access.
                        If multiple UoWs will access the shared session concurrently,
                        pass the same lock to all of them for safe coordination.

        WARNING: AsyncSession is NOT thread-safe or coroutine-safe for concurrent access.
        If you need to run multiple UoWs concurrently with a shared session, you MUST
        provide a shared_lock. Without it, concurrent operations may corrupt the session
        state, leading to data inconsistencies or errors.

        For truly parallel operations, consider using separate sessions instead.

        Example:
            # Safe sequential usage (no lock needed):
            async with uow1.configure_for_shared_session(session):
                await uow1.repo.get(...)
            async with uow2.configure_for_shared_session(session):
                await uow2.repo.get(...)

            # Concurrent usage (lock required):
            shared_lock = asyncio.Lock()
            async with uow1.configure_for_shared_session(session, shared_lock):
                async with shared_lock:
                    await uow1.repo.get(...)
        """
        self._session = session
        self._auto_commit = False
        self._owns_session = False
        self._shared_session_lock = shared_lock

        if shared_lock is None:
            self._logger.debug(
                "Shared session configured without lock - ensure sequential access only",
            )

        return self

    @property
    def shared_session_lock(self) -> asyncio.Lock | None:
        """Get the shared session lock for coordinating concurrent access."""
        return self._shared_session_lock

    @abstractmethod
    def _register_repositories(self) -> None:
        """Register feature-specific repositories."""
        ...

    @staticmethod
    def _raise_session_factory_error() -> None:
        """Raise error for missing session factory."""
        msg = "Session factory not provided"
        raise RuntimeError(msg)

    @timeout_uow_aware(SETTINGS.TIMEOUTS.UOW_CONTEXT_TIMEOUT)
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
        """Exit async context with automatic commit/rollback and cleanup.

        Rollback behavior:
        - On any exception: rollback if we own the session
        - On timeout: rollback and mark for cleanup delay
        - On success with auto_commit: commit
        - On success without auto_commit: leave transaction open for parent UoW
        """
        _start_time = time.perf_counter()
        try:
            # Detect timeout exceptions to trigger cleanup delay
            if exc_type and exc_val:
                from backend.core.exceptions.db_exceptions import DatabaseTimeoutError  # noqa: PLC0415
                from backend.core.exceptions.http_exceptions import TimeoutException  # noqa: PLC0415

                if isinstance(exc_val, (TimeoutException, DatabaseTimeoutError, asyncio.TimeoutError)):
                    self._timeout_occurred = True
                    self._logger.info("Timeout detected", exception_type=exc_type.__name__)

            if exc_type:
                # Always rollback on exception if we own the session
                # This ensures transactions are properly terminated even on timeout
                if self._owns_session:
                    await self._safe_rollback()
            elif self._auto_commit:
                await self.commit()
        finally:
            if self._owns_session:
                await self._cleanup()
            else:
                self._repositories.clear()
        _duration = time.perf_counter() - _start_time
        self._logger.info("UOW cleaned up", duration=f"{_duration:.5f}s")

    async def _safe_rollback(self) -> None:
        """Perform rollback with timeout protection to prevent hanging on broken connections."""
        if not self._session:
            return

        try:
            await asyncio.wait_for(
                self._session.rollback(),
                timeout=SETTINGS.TIMEOUTS.SESSION_CLOSE_TIMEOUT,
            )
            self._logger.debug("Transaction rolled back")
        except TimeoutError:
            self._logger.warning("Rollback timed out, connection may be in bad state")
            self._timeout_occurred = True
        except (OSError, RuntimeError) as e:
            self._logger.warning("Rollback failed", error=str(e))

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

    @timeout_uow_aware(SETTINGS.TIMEOUTS.TRANSACTION_TIMEOUT)
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
        """Rollback the current transaction.

        For internal use during __aexit__, prefer _safe_rollback() which has
        timeout protection. This method is for explicit rollback calls.
        """
        if not self._session:
            msg = "Cannot rollback without active session"
            raise RuntimeError(msg)

        try:
            await asyncio.wait_for(
                self._session.rollback(),
                timeout=SETTINGS.TIMEOUTS.SESSION_CLOSE_TIMEOUT,
            )
            self._logger.debug("Transaction rolled back")
        except TimeoutError:
            self._logger.warning("Rollback timed out")
            self._timeout_occurred = True
        except Exception as e:
            self._logger.exception("Rollback failed", error=str(e))

    @timeout_uow_aware(SETTINGS.TIMEOUTS.TRANSACTION_TIMEOUT)
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
