"""
Unit tests for Trusted Proxy Validation.

Tests critical paths:
- X-Forwarded-For header parsing
- Trusted proxy CIDR validation
- Client IP extraction from proxy chains
- Production CIDR validation
"""

from unittest.mock import patch

import pytest

from backend.core.security.proxy_validation import (
    _get_trusted_networks,
    _get_trusted_networks_cached,
    _parse_trusted_networks,
    extract_client_ip_secure,
    is_trusted_proxy,
)


pytestmark = [pytest.mark.unit]


class TestParseTrustedNetworks:
    """Tests for CIDR parsing."""

    def test_parse_ipv4_cidr(self):
        """Should parse IPv4 CIDR notation."""
        cidrs = ("10.0.0.0/8", "192.168.0.0/16")
        networks = _parse_trusted_networks(cidrs)

        assert len(networks) == 2

    def test_parse_ipv6_cidr(self):
        """Should parse IPv6 CIDR notation."""
        cidrs = ("::1/128", "2001:db8::/32")
        networks = _parse_trusted_networks(cidrs)

        assert len(networks) == 2

    def test_parse_mixed_cidrs(self):
        """Should parse mixed IPv4 and IPv6 CIDRs."""
        cidrs = ("10.0.0.0/8", "::1/128")
        networks = _parse_trusted_networks(cidrs)

        assert len(networks) == 2

    def test_invalid_cidr_raises_error(self):
        """Invalid CIDR should raise ConfigurationError."""
        from backend.core.security.proxy_validation import ConfigurationError

        cidrs = ("not_a_cidr",)

        with pytest.raises(ConfigurationError):
            _parse_trusted_networks(cidrs)


class TestIsTrustedProxy:
    """Tests for trusted proxy checking."""

    def test_localhost_trusted_by_default(self):
        """Localhost should be trusted in default configuration."""
        with patch("backend.core.security.proxy_validation.SETTINGS") as mock_settings:
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = ["127.0.0.0/8", "::1/128"]
            _get_trusted_networks_cached.cache_clear()

            assert is_trusted_proxy("127.0.0.1") is True
            assert is_trusted_proxy("127.0.0.2") is True

    def test_ipv6_localhost_trusted(self):
        """IPv6 localhost should be trusted."""
        with patch("backend.core.security.proxy_validation.SETTINGS") as mock_settings:
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = ["::1/128"]
            _get_trusted_networks_cached.cache_clear()

            assert is_trusted_proxy("::1") is True

    def test_untrusted_ip_returns_false(self):
        """IP outside trusted ranges should not be trusted."""
        with patch("backend.core.security.proxy_validation.SETTINGS") as mock_settings:
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = ["127.0.0.0/8"]
            _get_trusted_networks_cached.cache_clear()

            assert is_trusted_proxy("8.8.8.8") is False
            assert is_trusted_proxy("192.168.1.1") is False

    def test_empty_ip_not_trusted(self):
        """Empty or 'unknown' IP should not be trusted."""
        assert is_trusted_proxy("") is False
        assert is_trusted_proxy("unknown") is False

    def test_invalid_ip_format_not_trusted(self):
        """Invalid IP format should not be trusted."""
        assert is_trusted_proxy("not_an_ip") is False


class TestExtractClientIpSecure:
    """Tests for secure client IP extraction."""

    def test_no_xff_uses_direct_ip(self):
        """Without X-Forwarded-For, should use direct IP."""
        result = extract_client_ip_secure(None, "192.168.1.100")
        assert result == "192.168.1.100"

    def test_untrusted_source_ignores_xff(self):
        """X-Forwarded-For from untrusted source should be ignored."""
        with patch("backend.core.security.proxy_validation.SETTINGS") as mock_settings:
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = ["127.0.0.0/8"]
            _get_trusted_networks_cached.cache_clear()

            # Direct IP is not trusted, so XFF should be ignored
            result = extract_client_ip_secure(
                x_forwarded_for="10.0.0.1",
                direct_ip="8.8.8.8",  # Not trusted
            )
            assert result == "8.8.8.8"

    def test_trusted_proxy_uses_xff(self):
        """X-Forwarded-For from trusted proxy should be used."""
        with patch("backend.core.security.proxy_validation.SETTINGS") as mock_settings:
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = ["127.0.0.0/8"]
            _get_trusted_networks_cached.cache_clear()

            result = extract_client_ip_secure(
                x_forwarded_for="203.0.113.50",
                direct_ip="127.0.0.1",  # Trusted
            )
            assert result == "203.0.113.50"

    def test_proxy_chain_extracts_correct_client(self):
        """Should extract client IP from proxy chain correctly."""
        with patch("backend.core.security.proxy_validation.SETTINGS") as mock_settings:
            # Both 10.x.x.x and 127.x.x.x are trusted
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = ["10.0.0.0/8", "127.0.0.0/8"]
            _get_trusted_networks_cached.cache_clear()

            # Chain: client -> cloudflare (10.0.0.1) -> ALB (127.0.0.1)
            result = extract_client_ip_secure(
                x_forwarded_for="203.0.113.50, 10.0.0.1",
                direct_ip="127.0.0.1",
            )
            # Should return 203.0.113.50 (first non-trusted from right)
            assert result == "203.0.113.50"

    def test_spoofed_xff_prefix_ignored(self):
        """Spoofed XFF prefix should be ignored."""
        with patch("backend.core.security.proxy_validation.SETTINGS") as mock_settings:
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = ["10.0.0.0/8", "127.0.0.0/8"]
            _get_trusted_networks_cached.cache_clear()

            # Attacker prepends fake IP to XFF
            # Chain: spoofed, real_client, proxy
            result = extract_client_ip_secure(
                x_forwarded_for="1.2.3.4, 203.0.113.50, 10.0.0.1",
                direct_ip="127.0.0.1",
            )
            # Should return 203.0.113.50, NOT the spoofed 1.2.3.4
            assert result == "203.0.113.50"

    def test_all_trusted_uses_leftmost(self):
        """If all IPs are trusted (internal health check), use leftmost."""
        with patch("backend.core.security.proxy_validation.SETTINGS") as mock_settings:
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = ["10.0.0.0/8", "127.0.0.0/8"]
            _get_trusted_networks_cached.cache_clear()

            result = extract_client_ip_secure(
                x_forwarded_for="10.0.0.1, 10.0.0.2",
                direct_ip="127.0.0.1",
            )
            assert result == "10.0.0.1"

    def test_invalid_ip_in_xff_skipped(self):
        """Invalid IPs in XFF chain should be skipped."""
        with patch("backend.core.security.proxy_validation.SETTINGS") as mock_settings:
            mock_settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS = ["127.0.0.0/8"]
            _get_trusted_networks_cached.cache_clear()

            result = extract_client_ip_secure(
                x_forwarded_for="invalid_ip, 203.0.113.50",
                direct_ip="127.0.0.1",
            )
            assert result == "203.0.113.50"

    def test_missing_direct_ip_returns_unknown(self):
        """Missing direct IP should return 'unknown'."""
        result = extract_client_ip_secure(None, None)
        assert result == "unknown"


class TestProductionCidrValidation:
    """Tests for production CIDR configuration validation."""

    def test_production_rejects_default_cidrs_only(self):
        """Production should reject startup with only default CIDRs."""
        from backend.core.conf.settings import Settings

        with pytest.raises(ValueError) as exc_info:
            with patch.dict(
                "os.environ",
                {
                    "ENVIRONMENT": "prod",
                    "JWT_SECRET": "test_secret_that_is_long_enough_for_production_use",
                    "ADMIN_PASSWORD": "SecureP@ssw0rd!123456",
                    "ADMIN_MFA_ENABLED": "true",
                    "ADMIN_MFA_SECRET": "JBSWY3DPEHPK3PXP",
                    # Not setting TRUSTED_PROXY_CIDRS means using defaults
                },
            ):
                Settings()

        assert "TRUSTED_PROXY_CIDRS" in str(exc_info.value)

    def test_production_accepts_explicit_cidrs(self):
        """Production should accept explicitly configured CIDRs."""
        # This is more of an integration test - verifying the validator
        # doesn't reject when proper CIDRs are configured
        from backend.core.conf.settings import RateLimitSettings

        # Simulate production with explicit CIDR
        settings = RateLimitSettings(
            TRUSTED_PROXY_CIDRS=["10.0.0.0/16", "127.0.0.0/8"]
        )

        # Should not raise - CIDRs are not subset of defaults
        assert "10.0.0.0/16" in settings.TRUSTED_PROXY_CIDRS
