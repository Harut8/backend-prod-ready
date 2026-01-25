"""
Pure Domain Exceptions.

This module provides domain-level exceptions that are completely
independent of any framework (FastAPI, HTTP, etc.).

These exceptions should be:
- Used within the domain layer for business rule violations
- Caught and translated to HTTP exceptions in the application layer
- Framework-agnostic (no HTTP status codes, no response formatting)

Architecture:
    Domain Layer (this module)
        -> raises DomainError
    Application Layer (use cases/services)
        -> catches DomainError
        -> translates to appropriate HTTP exception

Example:
    # In domain layer:
    class User:
        def change_password(self, old_password: str, new_password: str):
            if not self.verify_password(old_password):
                raise DomainValidationError("Current password is incorrect")
            if len(new_password) < 8:
                raise DomainValidationError("Password must be at least 8 characters")
            self.password_hash = hash_password(new_password)

    # In application layer:
    async def change_password_use_case(...):
        try:
            user.change_password(old_password, new_password)
        except DomainValidationError as e:
            raise ValidationError(str(e)) from e  # HTTP 400
"""

from typing import Any


class DomainError(Exception):
    """
    Base exception for all domain-level errors.

    This is the root exception for the domain layer. All domain
    exceptions should inherit from this class.

    Use this for:
    - Business rule violations
    - Domain invariant violations
    - State transition errors
    - Entity not found (within domain context)

    Attributes:
        message: Human-readable error message
        code: Machine-readable error code for categorization
        context: Additional context about the error
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.code = code or self.__class__.__name__
        self.context = context or {}
        super().__init__(message)

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, code={self.code!r})"


class DomainValidationError(DomainError):
    """
    Exception for domain validation failures.

    Use when business rules or invariants are violated.
    This is pure domain validation - NOT input/request validation.

    Examples:
    - Email format validation is domain validation
    - Password strength rules are domain validation
    - Entity state constraints are domain validation

    Note: Input/request validation (JSON parsing, type coercion)
    should use Pydantic/FastAPI validation, not this exception.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        value: Any = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if field:
            ctx["field"] = field
        if value is not None:
            ctx["value"] = value
        super().__init__(message, code="VALIDATION_ERROR", context=ctx)
        self.field = field
        self.value = value


class EntityNotFoundError(DomainError):
    """
    Exception when a domain entity cannot be found.

    Use when an entity is required but doesn't exist in the domain.

    Note: This is different from HTTP 404. The domain layer doesn't
    know about HTTP - it just knows an entity wasn't found.
    """

    def __init__(
        self,
        entity_type: str,
        entity_id: Any,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        message = f"{entity_type} with id '{entity_id}' not found"
        ctx = context or {}
        ctx["entity_type"] = entity_type
        ctx["entity_id"] = str(entity_id)
        super().__init__(message, code="ENTITY_NOT_FOUND", context=ctx)
        self.entity_type = entity_type
        self.entity_id = entity_id


class DomainConflictError(DomainError):
    """
    Exception for domain-level conflicts.

    Use when an operation cannot be completed due to conflicting state:
    - Duplicate entity creation attempts
    - Concurrent modification conflicts
    - State transition conflicts
    """

    def __init__(
        self,
        message: str,
        *,
        entity_type: str | None = None,
        entity_id: Any = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if entity_type:
            ctx["entity_type"] = entity_type
        if entity_id is not None:
            ctx["entity_id"] = str(entity_id)
        super().__init__(message, code="CONFLICT", context=ctx)
        self.entity_type = entity_type
        self.entity_id = entity_id


class DomainStateError(DomainError):
    """
    Exception for invalid state transitions.

    Use when an entity is in a state that doesn't allow the requested operation.

    Example:
        class Order:
            def ship(self):
                if self.status != OrderStatus.PAID:
                    raise DomainStateError(
                        "Order cannot be shipped",
                        entity_type="Order",
                        current_state=self.status.value,
                        expected_states=["PAID"]
                    )
    """

    def __init__(
        self,
        message: str,
        *,
        entity_type: str | None = None,
        current_state: str | None = None,
        expected_states: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if entity_type:
            ctx["entity_type"] = entity_type
        if current_state:
            ctx["current_state"] = current_state
        if expected_states:
            ctx["expected_states"] = expected_states
        super().__init__(message, code="INVALID_STATE", context=ctx)
        self.entity_type = entity_type
        self.current_state = current_state
        self.expected_states = expected_states


class DomainAuthorizationError(DomainError):
    """
    Exception for domain-level authorization failures.

    Use when a domain operation is not allowed for the current actor,
    based on domain rules (not HTTP/API authorization).

    Example:
        class Document:
            def edit(self, editor: User):
                if editor.id != self.owner_id and not editor.is_admin:
                    raise DomainAuthorizationError(
                        "Only the owner or admin can edit this document",
                        actor_id=str(editor.id),
                        resource_type="Document",
                        action="edit"
                    )
    """

    def __init__(
        self,
        message: str,
        *,
        actor_id: str | None = None,
        resource_type: str | None = None,
        action: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if actor_id:
            ctx["actor_id"] = actor_id
        if resource_type:
            ctx["resource_type"] = resource_type
        if action:
            ctx["action"] = action
        super().__init__(message, code="AUTHORIZATION_FAILED", context=ctx)
        self.actor_id = actor_id
        self.resource_type = resource_type
        self.action = action


class DomainInvariantError(DomainError):
    """
    Exception when a domain invariant is violated.

    Domain invariants are rules that must always be true for an entity.
    This exception indicates a programming error or data corruption.

    Example:
        class Account:
            def withdraw(self, amount: Decimal):
                if self.balance - amount < Decimal("0"):
                    raise DomainInvariantError(
                        "Account balance cannot be negative",
                        invariant="balance >= 0"
                    )
    """

    def __init__(
        self,
        message: str,
        *,
        invariant: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if invariant:
            ctx["invariant"] = invariant
        super().__init__(message, code="INVARIANT_VIOLATED", context=ctx)
        self.invariant = invariant


class AggregateRootError(DomainError):
    """
    Exception for aggregate root violations.

    Use when operations violate aggregate root boundaries or rules.

    Example:
        Trying to modify a child entity directly instead of through
        its aggregate root.
    """

    def __init__(
        self,
        message: str,
        *,
        aggregate_type: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = context or {}
        if aggregate_type:
            ctx["aggregate_type"] = aggregate_type
        super().__init__(message, code="AGGREGATE_ERROR", context=ctx)
        self.aggregate_type = aggregate_type
