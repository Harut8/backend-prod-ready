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


@lru_cache(maxsize=1)
def _get_trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """
    Parse and cache trusted proxy CIDR networks.

    Returns:
        Tuple of parsed network objects for efficient IP matching.
    """
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []

    for cidr in SETTINGS.RATE_LIMIT.TRUSTED_PROXY_CIDRS:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
            networks.append(network)
        except ValueError as e:
            logger.warning("Invalid CIDR in TRUSTED_PROXY_CIDRS", cidr=cidr, error=str(e))

    return tuple(networks)


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
    ips = [ip.strip() for ip in x_forwarded_for.split(",")]

    if not ips:
        return direct_ip

    # The first IP in X-Forwarded-For is the original client
    # (when there's a single trusted proxy layer)
    client_ip = ips[0]

    # Validate the extracted IP format
    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        logger.warning(
            "Invalid IP in X-Forwarded-For header",
            x_forwarded_for=x_forwarded_for,
            extracted_ip=client_ip,
        )
        return direct_ip

    logger.debug(
        "Extracted client IP from trusted proxy",
        client_ip=client_ip,
        direct_ip=direct_ip,
    )
    return client_ip
