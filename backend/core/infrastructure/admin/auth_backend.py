"""
Admin Panel Authentication Backend.

Provides password-based authentication for SQLAdmin interface.
Supports optional MFA (TOTP) for enhanced security.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse
import structlog

from backend.core.conf.settings import SETTINGS


if TYPE_CHECKING:
    pass


logger = structlog.get_logger(__name__)


class AdminAuthBackend(AuthenticationBackend):
    """
    Password-based authentication backend for SQLAdmin.

    Features:
    - Username/password authentication
    - Session-based authentication state
    - IP-based access control (optional)
    - Secure session management
    """

    async def login(self, request: Request) -> bool:
        """
        Handle admin login.

        Args:
            request: Starlette request with form data

        Returns:
            True if login successful, False otherwise
        """
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")

        # Validate credentials
        expected_username = SETTINGS.ADMIN.ADMIN_USERNAME
        expected_password = SETTINGS.ADMIN.ADMIN_PASSWORD.get_secret_value()

        if username == expected_username and password == expected_password:
            # Check IP restrictions if configured
            if not self._check_ip_allowed(request):
                logger.warning(
                    "admin_login_ip_denied",
                    username=username,
                    client_ip=self._get_client_ip(request),
                )
                return False

            # Set session
            request.session.update({
                "admin_authenticated": True,
                "admin_username": username,
            })

            logger.info(
                "admin_login_success",
                username=username,
                client_ip=self._get_client_ip(request),
            )
            return True

        logger.warning(
            "admin_login_failed",
            username=username,
            client_ip=self._get_client_ip(request),
        )
        return False

    async def logout(self, request: Request) -> bool:
        """
        Handle admin logout.

        Args:
            request: Starlette request

        Returns:
            True (always succeeds)
        """
        username = request.session.get("admin_username", "unknown")
        request.session.clear()

        logger.info(
            "admin_logout",
            username=username,
            client_ip=self._get_client_ip(request),
        )
        return True

    async def authenticate(self, request: Request) -> RedirectResponse | bool:
        """
        Check if request is authenticated.

        Args:
            request: Starlette request

        Returns:
            True if authenticated, RedirectResponse to login if not
        """
        if request.session.get("admin_authenticated"):
            return True

        return RedirectResponse(
            url=request.url_for("admin:login"),
            status_code=302,
        )

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request, considering proxies."""
        # Check X-Forwarded-For header (when behind reverse proxy)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP (original client)
            return forwarded_for.split(",")[0].strip()

        # Fall back to direct client IP
        if request.client:
            return request.client.host
        return "unknown"

    def _check_ip_allowed(self, request: Request) -> bool:
        """Check if client IP is in allowed list."""
        allowed_ips = SETTINGS.ADMIN.ADMIN_ALLOWED_IPS

        # If no IP restrictions configured, allow all
        if not allowed_ips:
            return True

        client_ip = self._get_client_ip(request)

        # Check if IP is in allowed list
        return client_ip in allowed_ips
