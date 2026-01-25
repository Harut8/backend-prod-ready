import asyncio

from dependency_injector import containers, providers
import structlog

from backend.core.conf.settings import SETTINGS
from backend.core.infrastructure.cache.service import CacheService
from backend.core.infrastructure.database.connection import PgAsyncSQLAlchemyAdapter
from backend.core.infrastructure.database.session import SessionFactory


logger = structlog.get_logger(__name__)


class InfrastructureContainer(containers.DeclarativeContainer):
    """
    Dependency injection container for infrastructure services.

    Contains pure infrastructure components:
    - Database (PostgreSQL)
    - Cache (Redis)
    """

    config = providers.Configuration()

    # =====================================================================
    # DATABASE INFRASTRUCTURE
    # =====================================================================

    pg_db: providers.Singleton[PgAsyncSQLAlchemyAdapter] = providers.Singleton(
        PgAsyncSQLAlchemyAdapter,
        url=SETTINGS.DATABASE.DATABASE_URL,
        echo=SETTINGS.APP.LOG_LEVEL == "DEBUG",
        logger=logger.bind(component="pg_db"),
    )

    # Session factory for creating database sessions
    session_factory: providers.Singleton[SessionFactory] = providers.Singleton(
        SessionFactory,
        adapter=pg_db,
    )

    # =====================================================================
    # CACHE INFRASTRUCTURE
    # =====================================================================

    cache_service: providers.Singleton[CacheService] = providers.Singleton(CacheService)


async def shutdown_infrastructure(container: InfrastructureContainer, shutdown_timeout: float = 10.0) -> None:
    """
    Gracefully shutdown all infrastructure resources.
    """
    logger.info("Starting graceful shutdown of infrastructure resources")

    # Shutdown tasks to run in parallel
    _shutdown_tasks = []

    async def _close_redis() -> None:
        try:
            await asyncio.wait_for(
                asyncio.shield(container.cache_service().close()),
                timeout=5.0,
            )
        except TimeoutError:
            logger.warning("Redis shutdown timed out after 5s")
        except (ConnectionError, RuntimeError, AttributeError) as e:
            logger.warning("Error during Redis shutdown", error=str(e))

    _shutdown_tasks.append(_close_redis())

    async def _dispose_postgres() -> None:
        try:
            await asyncio.wait_for(
                asyncio.shield(container.pg_db().dispose()),
                timeout=5.0,
            )
        except TimeoutError:
            logger.warning("Postgres shutdown timed out after 5s")
        except (ConnectionError, RuntimeError) as e:
            logger.warning("Error during Postgres shutdown", error=str(e))

    _shutdown_tasks.append(_dispose_postgres())

    # Execute all shutdown tasks in parallel with overall timeout
    if _shutdown_tasks:
        task_count = len(_shutdown_tasks)
        logger.info("Executing shutdown tasks", task_count=task_count)
        try:
            await asyncio.wait_for(asyncio.gather(*_shutdown_tasks, return_exceptions=True), timeout=shutdown_timeout)
            logger.info("All shutdown tasks completed", task_count=task_count)
        except TimeoutError:
            logger.warning("Infrastructure shutdown timed out", timeout_seconds=shutdown_timeout)
        except (RuntimeError, ConnectionError) as e:
            logger.warning("Unexpected error during infrastructure shutdown", error=str(e))

    logger.info("Infrastructure resources shutdown completed")
