"""
System Health Check Services.

Application service that orchestrates health check operations.
Handles infrastructure concerns and returns domain objects.
"""

import asyncio
import time
from typing import TYPE_CHECKING

from sqlalchemy import text
import structlog

from backend.core.infrastructure.cache.service import CacheService
from backend.core.infrastructure.database.session import SessionFactory
from backend.features.system.domain.health import HealthStatus, SystemHealth


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class HealthCheckService:
    """
    Service for checking health of infrastructure components.

    Checks:
    - PostgreSQL database connectivity
    - Redis cache connectivity
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        cache_service: CacheService,
    ) -> None:
        self._session_factory = session_factory
        self._cache_service = cache_service

    async def check_database(self) -> HealthStatus:
        """
        Check database connectivity by executing a simple query.

        Returns:
            HealthStatus with connectivity result
        """

        start = time.perf_counter()
        try:
            session: AsyncSession = self._session_factory.create_session()
            async with session:
                await session.execute(text("SELECT 1"))
                latency_ms = (time.perf_counter() - start) * 1000
                logger.debug("Database health check passed", latency_ms=latency_ms)
                return HealthStatus(
                    healthy=True,
                    message="Database connection successful",
                    latency_ms=round(latency_ms, 2),
                )
        except Exception as e:  # noqa: BLE001
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning("Database health check failed", error=str(e), latency_ms=latency_ms)
            return HealthStatus(
                healthy=False,
                message=f"Database connection failed: {type(e).__name__}",
                latency_ms=round(latency_ms, 2),
            )

    async def check_cache(self) -> HealthStatus:
        """
        Check Redis cache connectivity.

        Returns:
            HealthStatus with connectivity result
        """

        start = time.perf_counter()
        try:
            is_healthy = await self._cache_service.ping()
            latency_ms = (time.perf_counter() - start) * 1000

            if is_healthy:
                logger.debug("Cache health check passed", latency_ms=latency_ms)
                return HealthStatus(
                    healthy=True,
                    message="Redis connection successful",
                    latency_ms=round(latency_ms, 2),
                )
            return HealthStatus(
                healthy=False,
                message="Redis client not available",
                latency_ms=round(latency_ms, 2),
            )
        except Exception as e:  # noqa: BLE001
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning("Cache health check failed", error=str(e), latency_ms=latency_ms)
            return HealthStatus(
                healthy=False,
                message=f"Redis connection failed: {type(e).__name__}",
                latency_ms=round(latency_ms, 2),
            )

    async def get_system_health(self) -> SystemHealth:
        """
        Get overall system health by checking all components.

        Checks are performed in parallel using asyncio.gather() to reduce
        overall latency.

        Returns:
            SystemHealth with status of all components
        """
        # Run health checks in parallel for reduced latency
        db_health, cache_health = await asyncio.gather(
            self.check_database(),
            self.check_cache(),
        )

        # Use factory method to create SystemHealth with computed status
        return SystemHealth.from_components(
            database=db_health,
            cache=cache_health,
        )
