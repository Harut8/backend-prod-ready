"""
System Health Check Services.

Provides health check capabilities for database and cache infrastructure.
"""

import asyncio
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING

from sqlalchemy import text
import structlog

from backend.core.infrastructure.cache.service import CacheService
from backend.core.infrastructure.database.session import SessionFactory


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HealthStatus:
    """Health status for a component."""

    healthy: bool
    message: str
    latency_ms: float | None = None


@dataclass(frozen=True)
class SystemHealth:
    """Overall system health status."""

    status: str  # "healthy", "degraded", "unhealthy"
    database: HealthStatus
    cache: HealthStatus

    @property
    def is_healthy(self) -> bool:
        """Check if all critical components are healthy."""
        return self.database.healthy

    @property
    def is_ready(self) -> bool:
        """Check if system is ready to accept traffic."""
        # Database is required, cache is optional (degraded mode)
        return self.database.healthy


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

        # Determine overall status
        if db_health.healthy and cache_health.healthy:
            status = "healthy"
        elif db_health.healthy:
            status = "degraded"  # Cache down but DB up
        else:
            status = "unhealthy"  # DB down

        return SystemHealth(
            status=status,
            database=db_health,
            cache=cache_health,
        )
