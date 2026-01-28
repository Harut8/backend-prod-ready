"""
System Health Mapper.

Handles conversions between domain objects and DTOs for the system feature.
Following Clean Architecture, mappers provide explicit layer boundary conversions.
"""

from backend.core.conf.settings import SETTINGS
from backend.features.system.domain.health import HealthStatus, SystemHealth
from backend.features.system.dto import (
    ComponentHealthDto,
    SystemHealthResponseDto,
    SystemStatusResponseDto,
)


class HealthMapper:
    """
    Mapper for system health domain objects to DTOs.

    Responsibilities:
    - Convert domain HealthStatus to ComponentHealthDto
    - Convert domain SystemHealth to SystemHealthResponseDto
    - Create simple status responses

    This mapper ensures domain objects never leak outside the feature boundary.
    """

    @staticmethod
    def to_component_dto(domain: HealthStatus) -> ComponentHealthDto:
        """
        Convert HealthStatus domain object to component DTO.

        Args:
            domain: The HealthStatus domain value object

        Returns:
            ComponentHealthDto ready for HTTP response
        """
        return ComponentHealthDto(
            healthy=domain.healthy,
            message=domain.message,
            latency_ms=domain.latency_ms,
        )

    @staticmethod
    def to_health_response_dto(domain: SystemHealth) -> SystemHealthResponseDto:
        """
        Convert SystemHealth domain object to response DTO.

        Args:
            domain: The SystemHealth domain entity

        Returns:
            SystemHealthResponseDto ready for HTTP response
        """
        return SystemHealthResponseDto(
            status=domain.status,
            service=SETTINGS.APP.APP_NAME,
            database=ComponentHealthDto(
                healthy=domain.database.healthy,
                message=domain.database.message,
                latency_ms=domain.database.latency_ms,
            ),
            cache=ComponentHealthDto(
                healthy=domain.cache.healthy,
                message=domain.cache.message,
                latency_ms=domain.cache.latency_ms,
            ),
        )

    @staticmethod
    def to_status_response_dto(status: str = "ok") -> SystemStatusResponseDto:
        """
        Create a simple status response DTO.

        Args:
            status: The status string (default: "ok")

        Returns:
            SystemStatusResponseDto for liveness checks
        """
        return SystemStatusResponseDto(
            status=status,
            service=SETTINGS.APP.APP_NAME,
        )
