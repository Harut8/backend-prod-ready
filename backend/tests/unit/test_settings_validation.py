"""
Unit tests for settings validation.

Tests critical security validations that protect production environments.
"""

import os
from unittest.mock import patch

import pytest


class TestTrustedProxyCIDRValidation:
    """Tests for TRUSTED_PROXY_CIDRS validation in production."""

    @pytest.fixture(autouse=True)
    def _clear_settings_cache(self):
        """Clear settings cache before each test."""
        from backend.core.conf.settings import get_settings

        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_overly_broad_cidr_rejected_in_production(self):
        """Overly broad CIDRs like 10.0.0.0/8 should be rejected in production."""
        env_vars = {
            "ENVIRONMENT": "prod",
            "JWT_SECRET": "test-secret-that-is-long-enough-for-validation-purposes-here",
            "ADMIN_PASSWORD": "SecureP@ssw0rd123!",
            "ADMIN_MFA_ENABLED": "true",
            "ADMIN_MFA_SECRET": "JBSWY3DPEHPK3PXP",
            "CORS_ORIGINS": '["https://example.com"]',
            "TRUSTED_PROXY_CIDRS": '["10.0.0.0/8"]',  # Too broad
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from backend.core.conf.settings import Settings

            with pytest.raises(ValueError, match="Overly broad CIDRs not allowed"):
                Settings()

    def test_overly_broad_192_168_rejected_in_production(self):
        """192.168.0.0/16 should be rejected in production."""
        env_vars = {
            "ENVIRONMENT": "prod",
            "JWT_SECRET": "test-secret-that-is-long-enough-for-validation-purposes-here",
            "ADMIN_PASSWORD": "SecureP@ssw0rd123!",
            "ADMIN_MFA_ENABLED": "true",
            "ADMIN_MFA_SECRET": "JBSWY3DPEHPK3PXP",
            "CORS_ORIGINS": '["https://example.com"]',
            "TRUSTED_PROXY_CIDRS": '["192.168.0.0/16"]',  # Too broad
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from backend.core.conf.settings import Settings

            with pytest.raises(ValueError, match="Overly broad CIDRs not allowed"):
                Settings()

    def test_specific_cidr_allowed_in_production(self):
        """Specific CIDRs like 10.0.1.0/24 should be allowed in production."""
        env_vars = {
            "ENVIRONMENT": "prod",
            "JWT_SECRET": "test-secret-that-is-long-enough-for-validation-purposes-here",
            "ADMIN_PASSWORD": "SecureP@ssw0rd123!",
            "ADMIN_MFA_ENABLED": "true",
            "ADMIN_MFA_SECRET": "JBSWY3DPEHPK3PXP",
            "CORS_ORIGINS": '["https://example.com"]',
            "TRUSTED_PROXY_CIDRS": '["10.0.1.0/24", "10.0.2.0/24"]',  # Specific subnets
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from backend.core.conf.settings import Settings

            # Should not raise
            settings = Settings()
            assert "10.0.1.0/24" in settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS

    def test_dev_only_defaults_rejected_in_production(self):
        """Dev-only default CIDRs should be rejected when no production CIDRs added."""
        env_vars = {
            "ENVIRONMENT": "prod",
            "JWT_SECRET": "test-secret-that-is-long-enough-for-validation-purposes-here",
            "ADMIN_PASSWORD": "SecureP@ssw0rd123!",
            "ADMIN_MFA_ENABLED": "true",
            "ADMIN_MFA_SECRET": "JBSWY3DPEHPK3PXP",
            "CORS_ORIGINS": '["https://example.com"]',
            "TRUSTED_PROXY_CIDRS": '["127.0.0.0/8"]',  # Only localhost
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from backend.core.conf.settings import Settings

            with pytest.raises(ValueError, match="must be explicitly configured for production"):
                Settings()

    def test_broad_cidrs_allowed_in_development(self):
        """Broad CIDRs should be allowed in development environment."""
        env_vars = {
            "ENVIRONMENT": "local",
            "TRUSTED_PROXY_CIDRS": '["10.0.0.0/8", "192.168.0.0/16"]',
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from backend.core.conf.settings import Settings

            # Should not raise in non-prod
            settings = Settings()
            assert "10.0.0.0/8" in settings.RATE_LIMIT.TRUSTED_PROXY_CIDRS


class TestJWTSecretValidation:
    """Tests for JWT secret validation."""

    @pytest.fixture(autouse=True)
    def _clear_settings_cache(self):
        """Clear settings cache before each test."""
        from backend.core.conf.settings import get_settings

        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_jwt_secret_required_in_production(self):
        """JWT_SECRET must be explicitly set in production."""
        env_vars = {
            "ENVIRONMENT": "prod",
            "JWT_SECRET": "",  # Empty - should fail
            "ADMIN_PASSWORD": "SecureP@ssw0rd123!",
            "ADMIN_MFA_ENABLED": "true",
            "ADMIN_MFA_SECRET": "JBSWY3DPEHPK3PXP",
            "CORS_ORIGINS": '["https://example.com"]',
            "TRUSTED_PROXY_CIDRS": '["10.0.1.0/24"]',
        }

        # Remove JWT_SECRET from env to test the validation
        with patch.dict(os.environ, env_vars, clear=False):
            # Clear the JWT_SECRET env var
            with patch.dict(os.environ, {"JWT_SECRET": ""}, clear=False):
                os.environ.pop("JWT_SECRET", None)

                from backend.core.conf.settings import Settings

                with pytest.raises(ValueError, match="JWT_SECRET must be explicitly set"):
                    Settings()


class TestAdminPasswordValidation:
    """Tests for admin password validation."""

    @pytest.fixture(autouse=True)
    def _clear_settings_cache(self):
        """Clear settings cache before each test."""
        from backend.core.conf.settings import get_settings

        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_weak_password_rejected_in_production(self):
        """Weak admin passwords should be rejected in production."""
        env_vars = {
            "ENVIRONMENT": "prod",
            "JWT_SECRET": "test-secret-that-is-long-enough-for-validation-purposes-here",
            "ADMIN_PASSWORD": "weak",  # Too short, no complexity
            "ADMIN_MFA_ENABLED": "true",
            "ADMIN_MFA_SECRET": "JBSWY3DPEHPK3PXP",
            "CORS_ORIGINS": '["https://example.com"]',
            "TRUSTED_PROXY_CIDRS": '["10.0.1.0/24"]',
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from backend.core.conf.settings import Settings

            with pytest.raises(ValueError, match="at least 16 characters"):
                Settings()

    def test_password_without_special_char_rejected(self):
        """Password without special characters should be rejected in production."""
        env_vars = {
            "ENVIRONMENT": "prod",
            "JWT_SECRET": "test-secret-that-is-long-enough-for-validation-purposes-here",
            "ADMIN_PASSWORD": "SecurePassword123456",  # No special char
            "ADMIN_MFA_ENABLED": "true",
            "ADMIN_MFA_SECRET": "JBSWY3DPEHPK3PXP",
            "CORS_ORIGINS": '["https://example.com"]',
            "TRUSTED_PROXY_CIDRS": '["10.0.1.0/24"]',
        }

        with patch.dict(os.environ, env_vars, clear=False):
            from backend.core.conf.settings import Settings

            with pytest.raises(ValueError, match="special character"):
                Settings()
