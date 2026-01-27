"""
Admin Panel Authentication Backend.

Extends identity-plan-kit's AdminAuthBackend with additional security features:
- IP allowlist enforcement
- Optional MFA (TOTP) support

Two-tier admin system (from IKP):
- Superadmin: Full permissions (create, edit, delete) - from env vars
- Admin: View-only permissions - users with 'admin' role in database
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from identity_plan_kit.admin import AdminAuthBackend as IPKAdminAuthBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse
import structlog

from backend.core.conf.settings import SETTINGS


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker


logger = structlog.get_logger(__name__)


class AdminAuthBackend(IPKAdminAuthBackend):
    """
    Extended authentication backend for SQLAdmin with IP allowlist.

    Inherits from IKP's AdminAuthBackend which provides:
    - Two-tier auth: superadmin (env vars) and admin (DB users)
    - Role-based permissions: can_create, can_edit, can_delete

    Adds:
    - IP-based access control (allowlist)
    - MFA support (TOTP)
    - Structured logging

    Features:
    - Superadmin: Full CRUD permissions (from ADMIN_EMAIL/ADMIN_PASSWORD)
    - Admin: View-only permissions (users with 'admin' role in database)
    - IP restrictions: Only allow access from configured IP addresses
    - Session-based authentication state
    """

    def __init__(
        self,
        secret_key: str,
        admin_email: str | None = None,
        admin_password: str | None = None,
        session_factory: "async_sessionmaker | None" = None,
    ) -> None:
        """
        Initialize the admin authentication backend.

        Args:
            secret_key: Secret key for session signing
            admin_email: Superadmin email (from env vars)
            admin_password: Superadmin password (from env vars)
            session_factory: SQLAlchemy async session factory for DB admin lookup
        """
        super().__init__(
            secret_key=secret_key,
            admin_email=admin_email,
            admin_password=admin_password,
            session_factory=session_factory,
        )

    async def login(self, request: Request) -> bool:
        """
        Handle admin login with IP restriction check.

        Extends IKP's login to add IP allowlist enforcement before
        credential validation.

        Args:
            request: Starlette request with form data

        Returns:
            True if login successful, False otherwise
        """
        # Check IP restrictions first
        if not self._check_ip_allowed(request):
            client_ip = self._get_client_ip(request)
            form = await request.form()
            email = form.get("username", "unknown")
            logger.warning(
                "admin_login_ip_denied",
                email=email,
                client_ip=client_ip,
                allowed_ips=SETTINGS.ADMIN.ADMIN_ALLOWED_IPS,
            )
            return False

        # Call parent login (handles superadmin and DB admin auth)
        result = await super().login(request)

        if result:
            # Add client IP to session for logging
            request.session["admin_client_ip"] = self._get_client_ip(request)

            # Check MFA if enabled
            if SETTINGS.ADMIN.MFA_ENABLED:
                # MFA verification would be handled in a separate step
                # For now, mark session as requiring MFA verification
                request.session["mfa_verified"] = False
                logger.info(
                    "admin_login_mfa_required",
                    email=request.session.get("admin_email"),
                    client_ip=self._get_client_ip(request),
                )

        return result

    async def logout(self, request: Request) -> bool:
        """
        Handle admin logout.

        Args:
            request: Starlette request

        Returns:
            True (always succeeds)
        """
        email = request.session.get("admin_email", "unknown")
        client_ip = request.session.get("admin_client_ip", self._get_client_ip(request))

        logger.info(
            "admin_logout",
            email=email,
            client_ip=client_ip,
        )

        return await super().logout(request)

    async def authenticate(self, request: Request) -> RedirectResponse | bool:
        """
        Check if request is authenticated.

        Extends IKP's authenticate to check IP restrictions and MFA.

        Args:
            request: Starlette request

        Returns:
            True if authenticated, RedirectResponse to login if not
        """
        # Check IP restrictions
        if not self._check_ip_allowed(request):
            logger.warning(
                "admin_access_ip_denied",
                client_ip=self._get_client_ip(request),
                path=str(request.url.path),
            )
            return RedirectResponse(
                url=request.url_for("admin:login"),
                status_code=302,
            )

        # Check MFA if enabled and session exists
        if (
            SETTINGS.ADMIN.MFA_ENABLED
            and request.session.get("admin_authenticated")
            and not request.session.get("mfa_verified", False)
        ):
            # Redirect to MFA verification page
            # For now, we just deny access - MFA page would be implemented separately
            logger.warning(
                "admin_access_mfa_required",
                email=request.session.get("admin_email"),
                client_ip=self._get_client_ip(request),
            )
            return RedirectResponse(
                url=request.url_for("admin:login"),
                status_code=302,
            )

        return await super().authenticate(request)

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
