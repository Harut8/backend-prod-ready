"""
Audit Logging for Admin Panel and Sensitive Operations.
ruff: noqa: UP037

This module provides audit logging capabilities for:
- Admin panel actions (user management, billing operations)
- Security-sensitive operations (login attempts, password changes)
- Data access auditing (who viewed what, when)
- Configuration changes

Audit logs are:
- Immutable (append-only)
- Timestamped with UTC time
- Include actor identity and IP address
- Stored in database for compliance/forensics

Usage:
    from backend.core.observability.audit import AuditLogger, AuditAction

    audit = AuditLogger()

    # Log an admin action
    await audit.log(
        action=AuditAction.USER_UPDATED,
        actor_id=admin_user_id,
        actor_ip=request.client.host,
        resource_type="User",
        resource_id=target_user_id,
        details={"field": "email", "old": "old@example.com", "new": "new@example.com"}
    )

    # Query audit logs
    logs = await audit.query(
        actor_id=admin_user_id,
        action=AuditAction.USER_UPDATED,
        start_date=datetime(2024, 1, 1),
    )
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import structlog
from uuid_extensions import uuid7

from backend.core.conf.settings import SETTINGS
from backend.core.infrastructure.database.models import Base


logger = structlog.get_logger(__name__)


class AuditAction(str, Enum):
    """
    Enumeration of auditable actions.

    Organize actions by category for easier filtering and reporting.
    """

    # Authentication actions
    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILED = "auth.login.failed"
    LOGOUT = "auth.logout"
    PASSWORD_CHANGED = "auth.password.changed"  # noqa: S105
    PASSWORD_RESET_REQUESTED = "auth.password.reset_requested"  # noqa: S105
    MFA_ENABLED = "auth.mfa.enabled"
    MFA_DISABLED = "auth.mfa.disabled"
    SESSION_REVOKED = "auth.session.revoked"

    # User management actions
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    USER_ACTIVATED = "user.activated"
    USER_DEACTIVATED = "user.deactivated"
    USER_ROLE_CHANGED = "user.role.changed"
    USER_VIEWED = "user.viewed"

    # Admin panel actions
    ADMIN_LOGIN = "admin.login"
    ADMIN_LOGOUT = "admin.logout"
    ADMIN_SETTINGS_CHANGED = "admin.settings.changed"
    ADMIN_EXPORT_DATA = "admin.export.data"

    # Billing actions
    SUBSCRIPTION_CREATED = "billing.subscription.created"
    SUBSCRIPTION_UPDATED = "billing.subscription.updated"
    SUBSCRIPTION_CANCELLED = "billing.subscription.cancelled"
    PAYMENT_PROCESSED = "billing.payment.processed"
    REFUND_ISSUED = "billing.refund.issued"
    INVOICE_GENERATED = "billing.invoice.generated"

    # Data access actions
    SENSITIVE_DATA_ACCESSED = "data.sensitive.accessed"
    DATA_EXPORTED = "data.exported"
    REPORT_GENERATED = "data.report.generated"

    # System actions
    CONFIG_CHANGED = "system.config.changed"
    FEATURE_FLAG_TOGGLED = "system.feature_flag.toggled"
    MAINTENANCE_MODE_TOGGLED = "system.maintenance.toggled"


class AuditLog(Base):
    """
    SQLAlchemy model for audit log entries.

    This table stores all audit events with full context for
    compliance, security analysis, and debugging.
    """

    __tablename__ = "audit_logs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    # Actor information
    actor_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    actor_type = Column(String(50), nullable=False, default="user")  # user, admin, system, api
    actor_ip = Column(String(45), nullable=True)  # IPv6 max length
    actor_user_agent = Column(Text, nullable=True)

    # Action information
    action = Column(String(100), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="success")  # success, failed, pending

    # Resource information
    resource_type = Column(String(100), nullable=True, index=True)
    resource_id = Column(String(100), nullable=True, index=True)

    # Additional context
    details = Column(JSONB, nullable=True)
    request_id = Column(String(100), nullable=True, index=True)

    # Indexes for common queries
    __table_args__ = (
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_actor_action", "actor_id", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )


class AuditLogger:
    """
    Audit logging service for tracking security-sensitive operations.

    Only persists to database if AUDIT_ENABLED=true in environment/settings.
    Always logs to stdout via structlog if AUDIT_LOG_TO_STDOUT=true.

    Features:
    - Async database persistence (when enabled)
    - Structured logging integration
    - Query capabilities for audit trails
    - Automatic context extraction
    - Sensitive field redaction
    """

    def __init__(self, session_factory: Any = None) -> None:
        """
        Initialize audit logger.

        Args:
            session_factory: SQLAlchemy async session factory
                           If None, logs only to structlog (for testing)
        """

        self._session_factory = session_factory
        self._enabled = SETTINGS.AUDIT.AUDIT_ENABLED
        self._log_to_stdout = SETTINGS.AUDIT.AUDIT_LOG_TO_STDOUT
        self._sensitive_fields = set(SETTINGS.AUDIT.AUDIT_SENSITIVE_FIELDS)

    def _redact_sensitive(self, details: dict[str, Any] | None) -> dict[str, Any] | None:
        """Redact sensitive fields from details."""
        if details is None:
            return None

        redacted: dict[str, Any] = {}
        for key, value in details.items():
            if key.lower() in self._sensitive_fields:
                redacted[key] = "[REDACTED]"
            elif isinstance(value, dict):
                result = self._redact_sensitive(value)
                redacted[key] = result if result is not None else {}
            else:
                redacted[key] = value
        return redacted

    async def log(
        self,
        action: AuditAction | str,
        *,
        actor_id: UUID | str | None = None,
        actor_type: str = "user",
        actor_ip: str | None = None,
        actor_user_agent: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        status: str = "success",
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> UUID | None:
        """
        Log an audit event.

        Only persists to database if AUDIT_ENABLED=true.

        Args:
            action: The action being audited
            actor_id: ID of the user/system performing the action
            actor_type: Type of actor (user, admin, system, api)
            actor_ip: IP address of the actor
            actor_user_agent: User agent string
            resource_type: Type of resource being acted upon
            resource_id: ID of the resource
            status: Action status (success, failed, pending)
            details: Additional context as JSON
            request_id: Request correlation ID

        Returns:
            UUID of the created audit log entry, or None if logging failed/disabled
        """
        action_str = action.value if isinstance(action, AuditAction) else action

        # Redact sensitive fields from details
        safe_details = self._redact_sensitive(details)

        # Log to structlog if enabled
        if self._log_to_stdout:
            log_context: dict[str, Any] = {
                "action": action_str,
                "actor_id": str(actor_id) if actor_id else None,
                "actor_type": actor_type,
                "actor_ip": actor_ip,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "status": status,
                "request_id": request_id,
            }
            if safe_details:
                log_context["details"] = safe_details

            logger.info("Audit event", **log_context)

        # Skip database persistence if disabled
        if not self._enabled:
            return None

        # Persist to database if session factory is available
        if self._session_factory is None:
            return None

        try:
            async with self._session_factory() as session:
                audit_entry = AuditLog(
                    actor_id=UUID(str(actor_id)) if actor_id else None,
                    actor_type=actor_type,
                    actor_ip=actor_ip,
                    actor_user_agent=actor_user_agent,
                    action=action_str,
                    status=status,
                    resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id else None,
                    details=safe_details,
                    request_id=request_id,
                )
                session.add(audit_entry)
                await session.commit()
                return UUID(str(audit_entry.id))
        except Exception as e:
            logger.exception(
                "Failed to persist audit log",
                error=str(e),
                action=action_str,
                actor_id=str(actor_id) if actor_id else None,
            )
            return None

    async def log_admin_action(
        self,
        action: AuditAction | str,
        admin_id: UUID | str,
        *,
        admin_ip: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> UUID | None:
        """
        Convenience method for logging admin panel actions.

        Automatically sets actor_type to "admin".
        """
        return await self.log(
            action=action,
            actor_id=admin_id,
            actor_type="admin",
            actor_ip=admin_ip,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            request_id=request_id,
        )

    async def log_system_action(
        self,
        action: AuditAction | str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> UUID | None:
        """
        Convenience method for logging system/automated actions.

        Automatically sets actor_type to "system".
        """
        return await self.log(
            action=action,
            actor_id=None,
            actor_type="system",
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            request_id=request_id,
        )

    async def query(
        self,
        session: AsyncSession,
        *,
        actor_id: UUID | str | None = None,
        action: AuditAction | str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        """
        Query audit logs with filters.

        Args:
            session: Database session
            actor_id: Filter by actor
            action: Filter by action type
            resource_type: Filter by resource type
            resource_id: Filter by resource ID
            start_date: Filter by start timestamp
            end_date: Filter by end timestamp
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            List of matching audit log entries
        """
        stmt = select(AuditLog)

        if actor_id:
            stmt = stmt.where(AuditLog.actor_id == UUID(str(actor_id)))
        if action:
            action_str = action.value if isinstance(action, AuditAction) else action
            stmt = stmt.where(AuditLog.action == action_str)
        if resource_type:
            stmt = stmt.where(AuditLog.resource_type == resource_type)
        if resource_id:
            stmt = stmt.where(AuditLog.resource_id == str(resource_id))
        if start_date:
            stmt = stmt.where(AuditLog.timestamp >= start_date)
        if end_date:
            stmt = stmt.where(AuditLog.timestamp <= end_date)

        stmt = stmt.order_by(AuditLog.timestamp.desc())
        stmt = stmt.limit(limit).offset(offset)

        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_actor_activity(
        self,
        session: AsyncSession,
        actor_id: UUID | str,
        *,
        days: int = 30,
        limit: int = 100,
    ) -> list[AuditLog]:
        """
        Get recent activity for a specific actor.

        Useful for security reviews and user activity audits.
        """
        start_date = datetime.now(UTC).replace(hour=0, minute=0, second=0)
        start_date = start_date.replace(day=max(1, start_date.day - days))

        return await self.query(
            session,
            actor_id=actor_id,
            start_date=start_date,
            limit=limit,
        )

    async def get_resource_history(
        self,
        session: AsyncSession,
        resource_type: str,
        resource_id: str,
        *,
        limit: int = 100,
    ) -> list[AuditLog]:
        """
        Get audit history for a specific resource.

        Useful for understanding what happened to a particular entity.
        """
        return await self.query(
            session,
            resource_type=resource_type,
            resource_id=resource_id,
            limit=limit,
        )
