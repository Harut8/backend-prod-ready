"""
Unit tests for Admin Authentication Backend.

Tests critical paths:
- IP allowlist with exact IP matching
- IP allowlist with CIDR range matching
- IPv4 and IPv6 support
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.core.infrastructure.admin.auth_backend import (
    AdminAuthBackend,
    _parse_admin_allowed_ips,
)


pytestmark = [pytest.mark.unit]


class TestParseAdminAllowedIps:
    """Tests for IP allowlist parsing."""

    def test_parse_exact_ips(self):
        """Should correctly parse exact IP addresses."""
        allowed = ("192.168.1.100", "10.0.0.1", "127.0.0.1")

        exact_ips, cidr_networks = _parse_admin_allowed_ips(allowed)

        assert "192.168.1.100" in exact_ips
        assert "10.0.0.1" in exact_ips
        assert "127.0.0.1" in exact_ips
        assert len(cidr_networks) == 0

    def test_parse_cidr_ranges(self):
        """Should correctly parse CIDR notation."""
        allowed = ("10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12")

        exact_ips, cidr_networks = _parse_admin_allowed_ips(allowed)

        assert len(exact_ips) == 0
        assert len(cidr_networks) == 3

    def test_parse_mixed_ips_and_cidrs(self):
        """Should handle mix of exact IPs and CIDR ranges."""
        allowed = ("127.0.0.1", "10.0.0.0/8", "192.168.1.50")

        exact_ips, cidr_networks = _parse_admin_allowed_ips(allowed)

        assert "127.0.0.1" in exact_ips
        assert "192.168.1.50" in exact_ips
        assert len(cidr_networks) == 1

    def test_parse_ipv6_addresses(self):
        """Should correctly parse IPv6 addresses."""
        allowed = ("::1", "2001:db8::/32", "fe80::1")

        exact_ips, cidr_networks = _parse_admin_allowed_ips(allowed)

        assert "::1" in exact_ips
        assert "fe80::1" in exact_ips
        assert len(cidr_networks) == 1  # The /32 CIDR

    def test_parse_ignores_invalid_entries(self):
        """Should skip invalid IP addresses/CIDRs without raising."""
        allowed = ("192.168.1.1", "invalid_ip", "10.0.0.0/8", "also_bad/24")

        exact_ips, cidr_networks = _parse_admin_allowed_ips(allowed)

        assert "192.168.1.1" in exact_ips
        assert len(cidr_networks) == 1  # Only valid CIDR
        assert "invalid_ip" not in exact_ips

    def test_parse_empty_list(self):
        """Should handle empty allowlist."""
        exact_ips, cidr_networks = _parse_admin_allowed_ips(())

        assert len(exact_ips) == 0
        assert len(cidr_networks) == 0

    def test_parse_result_is_cached(self):
        """Same input should return cached result."""
        allowed = ("192.168.1.1", "10.0.0.0/8")

        # Clear cache first
        _parse_admin_allowed_ips.cache_clear()

        result1 = _parse_admin_allowed_ips(allowed)
        result2 = _parse_admin_allowed_ips(allowed)

        # Should be same object (cached)
        assert result1 is result2


class TestAdminAuthBackendIpCheck:
    """Tests for AdminAuthBackend IP checking."""

    def create_mock_request(self, client_ip: str, x_forwarded_for: str | None = None) -> MagicMock:
        """Create a mock request with specified IP."""
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = client_ip
        request.headers = {}
        if x_forwarded_for:
            request.headers["X-Forwarded-For"] = x_forwarded_for
        return request

    def test_exact_ip_match_allowed(self):
        """Exact IP match should be allowed."""
        backend = AdminAuthBackend(secret_key="test")

        with patch("backend.core.infrastructure.admin.auth_backend.SETTINGS") as mock_settings:
            mock_settings.ADMIN.ADMIN_ALLOWED_IPS = ["192.168.1.100", "10.0.0.1"]
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = []

            request = self.create_mock_request("192.168.1.100")
            assert backend._check_ip_allowed(request) is True

            request = self.create_mock_request("10.0.0.1")
            assert backend._check_ip_allowed(request) is True

    def test_exact_ip_not_in_list_denied(self):
        """IP not in allowlist should be denied."""
        backend = AdminAuthBackend(secret_key="test")

        with patch("backend.core.infrastructure.admin.auth_backend.SETTINGS") as mock_settings:
            mock_settings.ADMIN.ADMIN_ALLOWED_IPS = ["192.168.1.100"]
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = []

            request = self.create_mock_request("192.168.1.101")  # Different IP
            assert backend._check_ip_allowed(request) is False

    def test_cidr_range_match_allowed(self):
        """IP within CIDR range should be allowed."""
        backend = AdminAuthBackend(secret_key="test")

        # Clear the cache to ensure fresh parsing
        _parse_admin_allowed_ips.cache_clear()

        with patch("backend.core.infrastructure.admin.auth_backend.SETTINGS") as mock_settings:
            mock_settings.ADMIN.ADMIN_ALLOWED_IPS = ["10.0.0.0/8"]
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = []

            # All 10.x.x.x addresses should be allowed
            request = self.create_mock_request("10.0.0.1")
            assert backend._check_ip_allowed(request) is True

            request = self.create_mock_request("10.255.255.255")
            assert backend._check_ip_allowed(request) is True

            request = self.create_mock_request("10.50.100.200")
            assert backend._check_ip_allowed(request) is True

    def test_cidr_range_outside_denied(self):
        """IP outside CIDR range should be denied."""
        backend = AdminAuthBackend(secret_key="test")

        _parse_admin_allowed_ips.cache_clear()

        with patch("backend.core.infrastructure.admin.auth_backend.SETTINGS") as mock_settings:
            mock_settings.ADMIN.ADMIN_ALLOWED_IPS = ["10.0.0.0/8"]
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = []

            # 192.x.x.x is outside 10.0.0.0/8
            request = self.create_mock_request("192.168.1.1")
            assert backend._check_ip_allowed(request) is False

    def test_empty_allowlist_allows_all(self):
        """Empty allowlist should allow all IPs."""
        backend = AdminAuthBackend(secret_key="test")

        with patch("backend.core.infrastructure.admin.auth_backend.SETTINGS") as mock_settings:
            mock_settings.ADMIN.ADMIN_ALLOWED_IPS = []
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = []

            request = self.create_mock_request("1.2.3.4")
            assert backend._check_ip_allowed(request) is True

    def test_ipv6_exact_match(self):
        """IPv6 exact address matching should work."""
        backend = AdminAuthBackend(secret_key="test")

        _parse_admin_allowed_ips.cache_clear()

        with patch("backend.core.infrastructure.admin.auth_backend.SETTINGS") as mock_settings:
            mock_settings.ADMIN.ADMIN_ALLOWED_IPS = ["::1", "fe80::1"]
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = []

            request = self.create_mock_request("::1")
            assert backend._check_ip_allowed(request) is True

            request = self.create_mock_request("fe80::1")
            assert backend._check_ip_allowed(request) is True

    def test_ipv6_cidr_match(self):
        """IPv6 CIDR matching should work."""
        backend = AdminAuthBackend(secret_key="test")

        _parse_admin_allowed_ips.cache_clear()

        with patch("backend.core.infrastructure.admin.auth_backend.SETTINGS") as mock_settings:
            mock_settings.ADMIN.ADMIN_ALLOWED_IPS = ["2001:db8::/32"]
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = []

            request = self.create_mock_request("2001:db8::1")
            assert backend._check_ip_allowed(request) is True

            request = self.create_mock_request("2001:db8:1234::5678")
            assert backend._check_ip_allowed(request) is True

            # Outside the range
            request = self.create_mock_request("2001:db9::1")
            assert backend._check_ip_allowed(request) is False

    def test_invalid_client_ip_denied(self):
        """Invalid client IP format should be denied."""
        backend = AdminAuthBackend(secret_key="test")

        _parse_admin_allowed_ips.cache_clear()

        with patch("backend.core.infrastructure.admin.auth_backend.SETTINGS") as mock_settings:
            mock_settings.ADMIN.ADMIN_ALLOWED_IPS = ["10.0.0.0/8"]
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = []

            request = self.create_mock_request("not_an_ip")
            assert backend._check_ip_allowed(request) is False
