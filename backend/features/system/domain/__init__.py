"""
System Feature Domain Layer.

Pure domain objects for system health and status.
"""

from backend.features.system.domain.health import (
    HealthStatus,
    SystemHealth,
)


__all__ = [
    "HealthStatus",
    "SystemHealth",
]
