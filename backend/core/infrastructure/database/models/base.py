from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Generic, TypeVar
import uuid

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid_extensions import uuid7


if TYPE_CHECKING:
    from backend.core.domain.base_domain import BaseDomain


class Base(DeclarativeBase):
    pass


T = TypeVar("T", bound="DbBaseModel[Any]")
DomainT = TypeVar("DomainT", bound="BaseDomain")


class DbBaseModel(Base, Generic[DomainT]):
    """Base ORM model with manual domain mapping."""

    __abstract__ = True

    @declared_attr
    def __tablename__(cls) -> str:
        return str(cls.__name__.lower() + "s")

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}

    @classmethod
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        _instance = cls()
        for key, value in data.items():
            if hasattr(_instance, key):
                setattr(_instance, key, value)
        return _instance

    def update(self, **kwargs: Any) -> "DbBaseModel[DomainT]":
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self

    def to_domain(self) -> DomainT:
        """
        Convert ORM model to domain model.

        Subclasses MUST override this method and implement explicit mapping logic.
        Use manual mapper functions from the feature's mappers module.
        """
        msg = (
            f"{self.__class__.__name__} must implement to_domain() method. "
            "Use explicit mapper functions from the feature's mappers module."
        )
        raise NotImplementedError(msg)

    @classmethod
    def from_domain(cls: type[T], domain: DomainT) -> T:
        """
        Convert domain model to ORM model.

        Subclasses MUST override this method and implement explicit mapping logic.
        Use manual mapper functions from the feature's mappers module.
        """
        msg = (
            f"{cls.__name__} must implement from_domain() class method. "
            "Use explicit mapper functions from the feature's mappers module."
        )
        raise NotImplementedError(msg)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(id={self.id})>"


class SoftDeleteMixin:
    """Mixin for soft delete functionality."""

    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,  # Index for efficient filtering on WHERE deleted_at IS NULL
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(UTC)

    def restore(self) -> None:
        self.deleted_at = None
