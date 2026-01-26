"""
Trusted proxy validation for secure X-Forwarded-For handling.

This module provides utilities to validate that requests claiming to originate
from specific IPs via X-Forwarded-For headers actually came through trusted
reverse proxies (nginx, AWS ALB, Cloudflare, etc.).

Without this validation, attackers can spoof X-Forwarded-For headers to bypass
IP-based rate limiting.
"""

from functools import lru_cache
import ipaddress

import structlog

from backend.core.conf.settings import SETTINGS


logger = structlog.get_logger(__name__)


class ConfigurationError(Exception):
    """Raised when security-critical configuration is invalid."""


def _parse_trusted_networks(
    cidrs: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """
    Parse trusted proxy CIDR networks.

    Args:
        cidrs: Tuple of CIDR strings to parse (hashable for caching).

    Returns:
        Tuple of parsed network objects for efficient IP matching.

    Raises:
        ConfigurationError: If any CIDR is invalid. Fails fast on startup
            to prevent security misconfigurations from going unnoticed.
    """
    _networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []

    for _cidr in cidrs:
        try:
            _network = ipaddress.ip_network(_cidr, strict=False)
            _networks.append(_network)
        except ValueError as e:
            _msg = f"Invalid CIDR '{_cidr}' in TRUSTED_PROXY_CIDRS: {e}"
            logger.error("Security configuration error", cidr=_cidr, error=str(e))
            raise ConfigurationError(_msg) from e

    return tuple(_networks)


@lru_cache(maxsize=1)
def _get_trusted_networks_cached(
    cidrs: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """
    Cached wrapper for parsing trusted networks.

    The cache key is the tuple of CIDRs, so cache invalidates automatically
    if configuration changes.
    """
    return _parse_trusted_networks(cidrs)


def _get_trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Get trusted networks with proper cache key from current settings."""
    return _get_trusted_networks_cached(tuple(SETTINGS.RATE_LIMIT.TRUSTED_PROXY_CIDRS))


def is_trusted_proxy(ip: str) -> bool:
    """
    Check if an IP address belongs to a trusted proxy network.

    Args:
        ip: IP address string to check (IPv4 or IPv6)

    Returns:
        True if the IP is within any trusted proxy CIDR range
    """
    if not ip or ip == "unknown":
        return False

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        logger.debug("Invalid IP address format", ip=ip)
        return False

    trusted_networks = _get_trusted_networks()

    return any(addr in network for network in trusted_networks)


def extract_client_ip_secure(
    x_forwarded_for: str | None,
    direct_ip: str | None,
) -> str:
    """
    Securely extract the real client IP from request headers.

    This function implements secure X-Forwarded-For handling:
    1. If the direct connection IP is from a trusted proxy, use X-Forwarded-For
    2. Otherwise, ignore X-Forwarded-For (could be spoofed) and use direct IP

    For proxy chains (e.g., "client, proxy1, proxy2"):
    - The rightmost IP added by our trusted proxy is the actual client
    - But if we only have one trusted proxy layer, the first IP is the client

    Args:
        x_forwarded_for: Value of X-Forwarded-For header (may be None)
        direct_ip: IP address from the direct TCP connection

    Returns:
        The validated client IP address, or "unknown" if unavailable
    """
    direct_ip = direct_ip or "unknown"

    # If no X-Forwarded-For header, use direct IP
    if not x_forwarded_for:
        return direct_ip

    # Only trust X-Forwarded-For if request came through a trusted proxy
    if not is_trusted_proxy(direct_ip):
        logger.debug(
            "Ignoring X-Forwarded-For from untrusted source",
            direct_ip=direct_ip,
            x_forwarded_for=x_forwarded_for,
        )
        return direct_ip

    # Parse the X-Forwarded-For header
    # Format: "client, proxy1, proxy2, ..." - leftmost is original client
    _ips = [ip.strip() for ip in x_forwarded_for.split(",")]

    if not _ips:
        return direct_ip

    # Walk from right to left to find the rightmost non-trusted-proxy IP
    # This handles multi-layer proxy chains (CDN -> Load Balancer -> App)
    # where each trusted proxy appends the previous hop's IP to the header.
    #
    # Example with Cloudflare -> AWS ALB -> App:
    #   X-Forwarded-For: "attacker_spoofed, real_client_ip, cloudflare_ip"
    #   Direct IP: alb_ip (trusted)
    #
    # Walking right-to-left:
    #   - cloudflare_ip is trusted -> skip
    #   - real_client_ip is NOT trusted -> this is the client
    #
    # This prevents attackers from prepending spoofed IPs to bypass rate limits.
    for _ip in reversed(_ips):
        _ip = _ip.strip()
        if not _ip:
            continue

        # Validate IP format
        try:
            ipaddress.ip_address(_ip)
        except ValueError:
            logger.warning(
                "Invalid IP in X-Forwarded-For header",
                x_forwarded_for=x_forwarded_for,
                invalid_ip=_ip,
            )
            continue

        # Return the first non-trusted-proxy IP from the right
        if not is_trusted_proxy(_ip):
            logger.debug(
                "Extracted client IP from trusted proxy chain",
                client_ip=_ip,
                direct_ip=direct_ip,
                x_forwarded_for=x_forwarded_for,
            )
            return _ip

    # All IPs in the chain are trusted proxies - use the leftmost
    # This handles edge cases like internal health checks
    _client_ip = _ips[0].strip()
    try:
        ipaddress.ip_address(_client_ip)
        logger.debug(
            "All IPs in chain are trusted, using leftmost",
            client_ip=_client_ip,
            direct_ip=direct_ip,
        )
        return _client_ip
    except ValueError:
        logger.warning(
            "Invalid leftmost IP in X-Forwarded-For header",
            x_forwarded_for=x_forwarded_for,
            extracted_ip=_client_ip,
        )
        return direct_ip
