"""
Admin Panel Setup Module.

Configures SQLAdmin with identity-plan-kit models and authentication.
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
    from sqlalchemy.ext.asyncio import AsyncEngine


logger = structlog.get_logger(__name__)


def setup_admin_panel(
    app: "FastAPI",
    engine: "AsyncEngine",
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

    Args:
        app: FastAPI application instance
        engine: SQLAlchemy async engine
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

    # Create authentication backend
    auth_backend = AdminAuthBackend(secret_key=session_secret)

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
        authentication_enabled=True,
    )

    return admin
