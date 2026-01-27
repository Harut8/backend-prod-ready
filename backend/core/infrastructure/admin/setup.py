"""
Admin Panel Setup Module.

Configures SQLAdmin with identity-plan-kit models and authentication.
Uses IKP's two-tier admin system with custom IP allowlist extension.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from identity_plan_kit.admin import setup_admin
from starlette.middleware.sessions import SessionMiddleware
import structlog

from backend.core.conf.settings import SETTINGS
from backend.core.infrastructure.admin.auth_backend import AdminAuthBackend


if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqladmin import Admin
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


logger = structlog.get_logger(__name__)


def setup_admin_panel(
    app: "FastAPI",
    engine: "AsyncEngine",
    session_factory: "async_sessionmaker | None" = None,
    *,
    base_url: str = "/admin",
    title: str | None = None,
) -> "Admin":
    """
    Set up SQLAdmin panel with identity-plan-kit models.

    Integrates:
    - Authentication (Users, OAuth Providers, Refresh Tokens)
    - RBAC (Roles, Permissions, Role-Permissions)
    - Plans (Plans, Features, Limits, Permissions, User Plans, Usage)

    Admin roles (from IKP):
    - Superadmin: Full permissions (create, edit, delete) - from ADMIN_EMAIL/PASSWORD
    - Admin: View-only permissions - users with 'admin' role in database

    Additional security (Kinonee extensions):
    - IP allowlist enforcement
    - MFA support (TOTP)

    Args:
        app: FastAPI application instance
        engine: SQLAlchemy async engine
        session_factory: SQLAlchemy async session factory for DB admin auth
        base_url: Base URL for admin panel (default: /admin)
        title: Admin panel title (defaults to app name)

    Returns:
        Configured Admin instance
    """
    # Add session middleware for admin authentication
    # Must be added before admin setup
    session_secret = SETTINGS.ADMIN.ADMIN_SESSION_SECRET.get_secret_value()

    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        session_cookie="admin_session",
        max_age=SETTINGS.ADMIN.ADMIN_SESSION_TIMEOUT_MINUTES * 60,
        same_site="lax",
        https_only=SETTINGS.APP.ENVIRONMENT == "prod",
    )

    # Create authentication backend with two-tier system
    # - Superadmin: from ADMIN_EMAIL/ADMIN_PASSWORD (full CRUD)
    # - Admin: from database users with 'admin' role (view-only)
    auth_backend = AdminAuthBackend(
        secret_key=session_secret,
        admin_email=SETTINGS.ADMIN.ADMIN_EMAIL,
        admin_password=SETTINGS.ADMIN.ADMIN_PASSWORD.get_secret_value(),
        session_factory=session_factory,  # Required for DB admin authentication
    )

    # Use identity-plan-kit's setup_admin which registers all model views
    admin_title = title or f"{SETTINGS.APP.APP_NAME} Admin"

    admin = setup_admin(
        app,
        engine,
        title=admin_title,
        base_url=base_url,
        authentication_backend=auth_backend,
    )

    logger.info(
        "Admin panel configured",
        base_url=base_url,
        title=admin_title,
        superadmin_email=SETTINGS.ADMIN.ADMIN_EMAIL,
        db_admin_enabled=session_factory is not None,
        ip_restrictions_enabled=bool(SETTINGS.ADMIN.ADMIN_ALLOWED_IPS),
        mfa_enabled=SETTINGS.ADMIN.MFA_ENABLED,
    )

    return admin
