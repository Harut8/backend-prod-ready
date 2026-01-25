"""
Dependency Injection Container for System Feature.

Provides health check services with injected infrastructure dependencies.
"""

from dependency_injector import containers, providers

from backend.core.infrastructure.cache.service import CacheService
from backend.core.infrastructure.database.session import SessionFactory
from backend.features.system.services import HealthCheckService


class SystemContainer(containers.DeclarativeContainer):
    """Dependency injection container for system feature."""

    # External dependencies (injected from infrastructure container)
    session_factory = providers.Dependency(instance_of=SessionFactory)
    cache_service = providers.Dependency(instance_of=CacheService)

    # Health check service (stateless - use Singleton for performance)
    health_check_service: providers.Singleton[HealthCheckService] = providers.Singleton(
        HealthCheckService,
        session_factory=session_factory,
        cache_service=cache_service,
    )
