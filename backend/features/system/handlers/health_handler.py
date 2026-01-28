"""
System Health and Status Endpoints.

Transport layer adapters for system health feature.
Handles HTTP-specific concerns and delegates business logic to services.

Provides endpoints for:
- Health checks (liveness probe)
- Readiness checks (dependency validation)
- System status information

Architecture notes:
- DTOs are used for request/response serialization
- Mappers convert between domain objects and DTOs
- Services return domain objects
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request, Response
import structlog

from backend.core.api.dtos.base import ResponseModel
from backend.core.security.rate_limiting import limiter
from backend.features.system.dependencies import SystemContainer
from backend.features.system.dto import (
    SystemHealthResponseDto,
    SystemStatusResponseDto,
)
from backend.features.system.mappers import HealthMapper
from backend.features.system.services import HealthCheckService


logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/system")


@router.get("/health", operation_id="system_health_check")
@limiter.limit("60/minute")
async def health(request: Request) -> ResponseModel[SystemStatusResponseDto]:  # noqa: ARG001
    """
    Health check endpoint (liveness probe).

    Returns a simple status indicating the service is running.
    Use this for Kubernetes liveness probes.

    This endpoint does NOT check dependencies - it only confirms
    the service process is alive and responding.
    """
    return ResponseModel.ok(data=HealthMapper.to_status_response_dto())


@router.get("/ready", operation_id="system_ready_check")
@limiter.limit("60/minute")
@inject
async def ready(
    request: Request,  # noqa: ARG001
    response: Response,
    health_check_service: Annotated[
        HealthCheckService,
        Depends(Provide[SystemContainer.health_check_service]),
    ],
) -> ResponseModel[SystemHealthResponseDto]:
    """
    Readiness check endpoint (readiness probe).

    Validates that the service is ready to accept traffic by checking:
    - Database connectivity
    - Redis cache connectivity

    Use this for Kubernetes readiness probes.

    Returns:
        - 200 OK: All critical dependencies are healthy
        - 503 Service Unavailable: Critical dependencies are unhealthy
    """
    system_health = await health_check_service.get_system_health()
    response_dto = HealthMapper.to_health_response_dto(system_health)

    if not system_health.is_ready:
        response.status_code = 503
        return ResponseModel.error(
            message="Service not ready",
            data=response_dto,
        )

    return ResponseModel.ok(data=response_dto)


@router.get("/status", operation_id="system_detailed_status")
@limiter.limit("30/minute")
@inject
async def status(
    request: Request,  # noqa: ARG001
    health_check_service: Annotated[
        HealthCheckService,
        Depends(Provide[SystemContainer.health_check_service]),
    ],
) -> ResponseModel[SystemHealthResponseDto]:
    """
    Detailed system status endpoint.

    Returns comprehensive health information about all system components.
    Use this for monitoring dashboards and debugging.
    """
    system_health = await health_check_service.get_system_health()
    return ResponseModel.ok(data=HealthMapper.to_health_response_dto(system_health))
