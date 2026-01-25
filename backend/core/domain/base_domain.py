from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from backend.core.domain.exceptions import DomainValidationError


@dataclass
class BaseDomain:
    """
    Base domain model for business logic.
    - Auto-generated __init__, __repr__, __eq__, __hash__
    All domain models inherit from this.
    """

    id: UUID
    created_at: datetime
    updated_at: datetime
    _validation_errors: list[str] = field(default_factory=list, init=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert domain to dict for cache serialization.

        Handles enums by converting to values.
        Used primarily by cache layer.
        """

        def _convert_value(value: Any) -> Any:
            """Convert special types for JSON serialization."""
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, BaseDomain):
                return value.to_dict()
            if isinstance(value, list):
                return [_convert_value(item) for item in value]
            return value

        data = asdict(self)
        return {k: _convert_value(v) for k, v in data.items() if not k.startswith("_")}

    def __eq__(self, other: object) -> bool:
        """Compare domain models by ID."""
        if not isinstance(other, BaseDomain):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash domain model by ID."""
        return hash(self.id)

    def add_validation_error(self, error: str) -> None:
        """Add a validation error message."""
        self._validation_errors.append(error)

    def get_validation_errors(self) -> list[str]:
        """Get all validation errors."""
        return self._validation_errors.copy()

    def raise_if_invalid(self) -> None:
        """Raise DomainValidationError if there are any validation errors."""
        if self._validation_errors:
            error_messages = "; ".join(self._validation_errors)
            raise DomainValidationError(error_messages)
