"""
System Feature DTOs.

Data Transfer Objects for system health and status endpoints.
"""

from backend.core.api.dtos.base import BaseResponseDto


class ComponentHealthDto(BaseResponseDto):
    """Health status for a single component."""

    healthy: bool
    message: str
    latency_ms: float | None = None


class SystemHealthResponseDto(BaseResponseDto):
    """Response DTO for detailed system health."""

    status: str
    service: str
    database: ComponentHealthDto
    cache: ComponentHealthDto


class SystemStatusResponseDto(BaseResponseDto):
    """Response DTO for simple system status endpoints."""

    status: str
    service: str
