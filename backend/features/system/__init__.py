"""
System Feature - Health Checks and Status Endpoints.

This feature provides infrastructure health monitoring and status endpoints
for Kubernetes probes (liveness, readiness) and operational dashboards.

Feature Structure:
    features/system/
    ├── domain/          # Domain entities (HealthStatus, SystemHealth)
    ├── dto/             # API response schemas
    ├── mappers/         # Domain ↔ DTO conversions
    ├── services/        # Health check orchestration
    ├── handlers/        # HTTP handlers
    └── dependencies.py  # DI wiring

Architecture Note:
    This feature follows Clean Architecture with folder-based structure:
    - Domain layer contains pure value objects for health state
    - No persistence layer (status is computed on-demand)
    - Mappers provide explicit boundary between domain and DTOs
"""

from backend.features.system.dependencies import SystemContainer
from backend.features.system.domain import HealthStatus, SystemHealth
from backend.features.system.handlers import system_router
from backend.features.system.mappers import HealthMapper
from backend.features.system.services import HealthCheckService


__all__ = [
    "HealthCheckService",
    "HealthMapper",
    "HealthStatus",
    "SystemContainer",
    "SystemHealth",
    "system_router",
]
