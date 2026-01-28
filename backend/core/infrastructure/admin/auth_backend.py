"""
Admin Panel Authentication Backend.

Extends identity-plan-kit's AdminAuthBackend with additional security features:
- IP allowlist enforcement (supports both exact IPs and CIDR ranges)
- Optional MFA (TOTP) support

Two-tier admin system (from IKP):
- Superadmin: Full permissions (create, edit, delete) - from env vars
- Admin: View-only permissions - users with 'admin' role in database
"""

from __future__ import annotations

from functools import lru_cache
import ipaddress
from typing import TYPE_CHECKING

from identity_plan_kit.admin import AdminAuthBackend as IPKAdminAuthBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse
import structlog

from backend.core.conf.settings import SETTINGS
from backend.core.security.proxy_validation import extract_client_ip_secure


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker


logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _parse_admin_allowed_ips(
    allowed_ips: tuple[str, ...],
) -> tuple[
    set[str],  # Exact IPs
    tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],  # CIDR networks
]:
    """
    Parse admin allowed IPs into exact matches and CIDR networks.

    Args:
        allowed_ips: Tuple of IP addresses or CIDR ranges (tuple for hashability)

    Returns:
        Tuple of (exact_ips set, cidr_networks tuple)
    """
    exact_ips: set[str] = set()
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []

    for entry in allowed_ips:
        entry = entry.strip()
        if not entry:
            continue

        # Check if it's a CIDR notation
        if "/" in entry:
            try:
                network = ipaddress.ip_network(entry, strict=False)
                networks.append(network)
                logger.debug("Parsed admin CIDR allowlist entry", cidr=entry)
            except ValueError as e:
                logger.warning("Invalid CIDR in ADMIN_ALLOWED_IPS", entry=entry, error=str(e))
        else:
            # Exact IP match
            try:
                # Validate it's a valid IP
                ipaddress.ip_address(entry)
                exact_ips.add(entry)
            except ValueError as e:
                logger.warning("Invalid IP in ADMIN_ALLOWED_IPS", entry=entry, error=str(e))

    return exact_ips, tuple(networks)


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
        """Extract client IP from request securely.

        SECURITY: Only trusts X-Forwarded-For headers from configured trusted proxies.
        This prevents IP spoofing attacks to bypass admin IP allowlist.
        """
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        direct_ip = request.client.host if request.client else None
        return extract_client_ip_secure(x_forwarded_for, direct_ip)

    def _check_ip_allowed(self, request: Request) -> bool:
        """Check if client IP is in allowed list (supports exact IPs and CIDR ranges).

        The allowed list can contain:
        - Exact IP addresses: "192.168.1.100"
        - CIDR ranges: "10.0.0.0/8", "192.168.0.0/16"

        Both IPv4 and IPv6 are supported.
        """
        allowed_ips = SETTINGS.ADMIN.ADMIN_ALLOWED_IPS

        # If no IP restrictions configured, allow all
        if not allowed_ips:
            return True

        client_ip = self._get_client_ip(request)

        # Parse allowed IPs into exact matches and CIDR networks
        # Uses cached parsing to avoid re-parsing on every request
        exact_ips, cidr_networks = _parse_admin_allowed_ips(tuple(allowed_ips))

        # Check exact IP match first (fast path)
        if client_ip in exact_ips:
            return True

        # Check CIDR networks
        try:
            client_addr = ipaddress.ip_address(client_ip)
            for network in cidr_networks:
                if client_addr in network:
                    return True
        except ValueError:
            # Invalid client IP format - deny access
            logger.warning("Invalid client IP format during admin access check", client_ip=client_ip)
            return False

        return False
