"""
System Feature - Health Checks and Status Endpoints.

This feature provides infrastructure health monitoring and status endpoints
for Kubernetes probes (liveness, readiness) and operational dashboards.

Architecture Note:
    Unlike other features, the system feature intentionally lacks the standard
    domain/, models/, and repositories/ subdirectories. This is because:

    1. No Domain Logic: Health checks are purely infrastructure concerns with
       no business rules or domain invariants to enforce.

    2. No Persistence: System status is transient and computed on-demand from
       infrastructure state. There are no entities to persist or retrieve.

    3. Infrastructure Focus: This feature exists at the infrastructure boundary,
       not within the domain layer. It queries other services (database, cache)
       rather than managing its own domain objects.

    This structural exception follows Clean Architecture principles by keeping
    infrastructure concerns separate from domain concerns.
"""

from backend.features.system.dependencies import SystemContainer
from backend.features.system.router import system_router
from backend.features.system.services import HealthCheckService, HealthStatus, SystemHealth


__all__ = [
    "HealthCheckService",
    "HealthStatus",
    "SystemContainer",
    "SystemHealth",
    "system_router",
]
