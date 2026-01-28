"""
System Health Domain Entities.

Pure domain objects representing system health concepts.
No framework or infrastructure dependencies.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HealthStatus:
    """
    Health status for a single component.

    This is a value object representing the health state of
    a system component (database, cache, etc.).
    """

    healthy: bool
    message: str
    latency_ms: float | None = None


@dataclass(frozen=True)
class SystemHealth:
    """
    Overall system health status.

    Aggregates health information from all system components
    and provides computed properties for readiness checks.
    """

    status: str  # "healthy", "degraded", "unhealthy"
    database: HealthStatus
    cache: HealthStatus

    @property
    def is_healthy(self) -> bool:
        """Check if all critical components are healthy."""
        return self.database.healthy

    @property
    def is_ready(self) -> bool:
        """
        Check if system is ready to accept traffic.

        Database is required, cache is optional (degraded mode).
        """
        return self.database.healthy

    @classmethod
    def from_components(
        cls,
        database: HealthStatus,
        cache: HealthStatus,
    ) -> "SystemHealth":
        """
        Factory method to create SystemHealth from component statuses.

        Automatically determines overall status based on component health.

        Args:
            database: Database health status
            cache: Cache health status

        Returns:
            SystemHealth with computed overall status
        """
        if database.healthy and cache.healthy:
            status = "healthy"
        elif database.healthy:
            status = "degraded"  # Cache down but DB up
        else:
            status = "unhealthy"  # DB down

        return cls(
            status=status,
            database=database,
            cache=cache,
        )
