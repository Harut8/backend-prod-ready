"""
Feature flag service for runtime feature control and kill switches.

Enables quick feature disable without deployment, useful for:
- Emergency kill switches for problematic features
- Gradual rollouts
- A/B testing
- Maintenance mode toggles
"""

from enum import Enum

import structlog

from backend.core.infrastructure.cache.service import CacheService


logger = structlog.get_logger(__name__)


class FeatureFlag(str, Enum):
    """
    Predefined feature flags for the application.

    Add new flags here as needed. Naming convention: FEATURE_NAME
    """

    # Payment/Billing features
    PADDLE_WEBHOOKS_ENABLED = "paddle_webhooks_enabled"
    STRIPE_WEBHOOKS_ENABLED = "stripe_webhooks_enabled"
    SUBSCRIPTION_RENEWALS_ENABLED = "subscription_renewals_enabled"

    # Communication features
    TELEGRAM_NOTIFICATIONS_ENABLED = "telegram_notifications_enabled"
    EMAIL_NOTIFICATIONS_ENABLED = "email_notifications_enabled"

    # API features
    RATE_LIMITING_ENABLED = "rate_limiting_enabled"
    NEW_USER_REGISTRATION_ENABLED = "new_user_registration_enabled"

    # Maintenance
    MAINTENANCE_MODE = "maintenance_mode"
    READ_ONLY_MODE = "read_only_mode"


class FeatureFlagService:
    """
    Redis-backed feature flag service for runtime feature control.

    Features are enabled by default unless explicitly disabled.
    This fail-open behavior ensures features work even if Redis is unavailable.

    Usage:
        flags = FeatureFlagService(cache_service)

        if await flags.is_enabled(FeatureFlag.PADDLE_WEBHOOKS_ENABLED):
            process_paddle_webhook(data)
        else:
            logger.info("Paddle webhooks disabled via feature flag")

        # Disable a feature (kill switch)
        await flags.set_flag(FeatureFlag.PADDLE_WEBHOOKS_ENABLED, enabled=False)
    """

    CACHE_PREFIX = "feature_flag"
    DEFAULT_TTL = 3600  # 1 hour cache for flag values

    def __init__(self, cache_service: CacheService) -> None:
        self._cache = cache_service

    def _build_key(self, flag: FeatureFlag) -> str:
        """Build the cache key for a feature flag."""
        return f"{self.CACHE_PREFIX}:{flag.value}"

    async def is_enabled(self, flag: FeatureFlag, *, default: bool = True) -> bool:
        """
        Check if a feature flag is enabled.

        Args:
            flag: The feature flag to check
            default: Default value if flag is not set (default: True - fail open)

        Returns:
            True if feature is enabled, False otherwise
        """
        _key = self._build_key(flag)
        _value = await self._cache.get(_key)

        if _value is None:
            # Flag not set - use default (fail open)
            logger.debug("Feature flag not set, using default", flag=flag.value, default=default)
            return default

        # Parse the stored value
        _enabled = _value.lower() in ("true", "1", "yes", "enabled")
        logger.debug("Feature flag checked", flag=flag.value, enabled=_enabled)
        return _enabled

    async def is_disabled(self, flag: FeatureFlag, *, default: bool = True) -> bool:
        """
        Check if a feature flag is disabled.

        Convenience method - inverse of is_enabled().
        """
        return not await self.is_enabled(flag, default=default)

    async def set_flag(self, flag: FeatureFlag, *, enabled: bool) -> None:
        """
        Set a feature flag value.

        Args:
            flag: The feature flag to set
            enabled: Whether the feature should be enabled
        """
        _key = self._build_key(flag)
        _value = "true" if enabled else "false"
        await self._cache.set(_key, _value, ttl=self.DEFAULT_TTL)

        logger.info(
            "Feature flag updated",
            flag=flag.value,
            enabled=enabled,
        )

    async def clear_flag(self, flag: FeatureFlag) -> None:
        """
        Clear a feature flag, reverting to default behavior.

        After clearing, is_enabled() will return the default value.
        """
        _key = self._build_key(flag)
        await self._cache.delete(_key)

        logger.info("Feature flag cleared", flag=flag.value)

    async def get_all_flags(self) -> dict[str, bool]:
        """
        Get the current state of all known feature flags.

        Returns a dict mapping flag names to their enabled status.
        Flags not explicitly set will show their default value (True).
        """
        _flags: dict[str, bool] = {}
        for _flag in FeatureFlag:
            _flags[_flag.value] = await self.is_enabled(_flag)
        return _flags

    async def set_maintenance_mode(self, *, enabled: bool) -> None:
        """
        Enable or disable maintenance mode.

        Convenience method for the common maintenance mode toggle.
        """
        await self.set_flag(FeatureFlag.MAINTENANCE_MODE, enabled=enabled)
        if enabled:
            logger.warning("Maintenance mode ENABLED - some features may be unavailable")
        else:
            logger.info("Maintenance mode disabled")

    async def is_maintenance_mode(self) -> bool:
        """Check if maintenance mode is enabled (default: False)."""
        return await self.is_enabled(FeatureFlag.MAINTENANCE_MODE, default=False)
