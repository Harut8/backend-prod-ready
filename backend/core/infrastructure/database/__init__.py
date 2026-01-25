"""
Database infrastructure layer.

Provides ORM models, repositories, unit of work, and connection management.
"""

from backend.core.infrastructure.database.error_handler import database_error_handler
from backend.core.infrastructure.database.models import DbBaseModel, SoftDeleteMixin
from backend.core.infrastructure.database.repositories import PgBaseRepository
from backend.core.infrastructure.database.session import SessionFactory
from backend.core.infrastructure.database.uow import BaseUnitOfWork


__all__ = [
    # Unit of Work
    "BaseUnitOfWork",
    # Models
    "DbBaseModel",
    # Repository
    "PgBaseRepository",
    # Session
    "SessionFactory",
    "SoftDeleteMixin",
    # Error handling
    "database_error_handler",
]
