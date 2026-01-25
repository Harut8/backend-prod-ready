"""
Core infrastructure services.

Provides database, cache, dependency injection container, and other infrastructure components.
"""

from backend.core.infrastructure.cache import CacheService
from backend.core.infrastructure.container import InfrastructureContainer, shutdown_infrastructure
from backend.core.infrastructure.database import (
    BaseUnitOfWork,
    DbBaseModel,
    PgBaseRepository,
    SessionFactory,
    SoftDeleteMixin,
    database_error_handler,
)


__all__ = [
    "BaseUnitOfWork",
    # Cache
    "CacheService",
    # Database
    "DbBaseModel",
    # Container
    "InfrastructureContainer",
    "PgBaseRepository",
    "SessionFactory",
    "SoftDeleteMixin",
    "database_error_handler",
    "shutdown_infrastructure",
]
