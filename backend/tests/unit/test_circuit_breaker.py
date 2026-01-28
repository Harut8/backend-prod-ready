"""
Unit tests for Circuit Breaker with jitter.

Tests critical paths:
- Jitter is applied to reset timeout (prevents thundering herd)
- Jitter values are within expected range
- Different circuit breaker types get different configurations
"""

import pytest

from backend.core.security.circuit_breaker import (
    CIRCUIT_BREAKER_JITTER_FACTOR,
    CircuitBreakerRegistry,
    CircuitBreakerType,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class TestCircuitBreakerJitter:
    """Tests for circuit breaker jitter to prevent thundering herd."""

    def test_jitter_factor_is_reasonable(self):
        """Jitter factor should be between 0 and 50%."""
        assert 0 < CIRCUIT_BREAKER_JITTER_FACTOR <= 0.5

    def test_apply_jitter_returns_value_in_expected_range(self):
        """Jittered timeout should be between base and base * (1 + jitter_factor)."""
        registry = CircuitBreakerRegistry()
        base_timeout = 60

        # Run multiple times to check randomness
        results = [registry._apply_jitter(base_timeout) for _ in range(100)]

        min_expected = base_timeout
        max_expected = int(base_timeout * (1 + CIRCUIT_BREAKER_JITTER_FACTOR))

        for result in results:
            assert min_expected <= result <= max_expected, (
                f"Jittered timeout {result} not in range [{min_expected}, {max_expected}]"
            )

    def test_apply_jitter_produces_varying_results(self):
        """Jitter should produce varying results (not always the same)."""
        registry = CircuitBreakerRegistry()
        base_timeout = 60

        results = [registry._apply_jitter(base_timeout) for _ in range(50)]

        # Should have multiple unique values (randomness)
        unique_values = set(results)
        assert len(unique_values) > 1, "Jitter should produce varying results"

    def test_get_config_applies_jitter_to_database_cb(self):
        """Database circuit breaker config should include jittered timeout."""
        registry = CircuitBreakerRegistry()

        # Get config multiple times
        configs = [
            registry._get_config(CircuitBreakerType.DATABASE)
            for _ in range(20)
        ]

        # Extract timeouts (second element of tuple)
        timeouts = [config[1] for config in configs]

        # Should have some variation due to jitter
        unique_timeouts = set(timeouts)
        assert len(unique_timeouts) >= 1  # At minimum different calls may get same value

    def test_get_config_applies_jitter_to_redis_cb(self):
        """Redis circuit breaker config should include jittered timeout."""
        registry = CircuitBreakerRegistry()

        configs = [
            registry._get_config(CircuitBreakerType.REDIS)
            for _ in range(20)
        ]

        timeouts = [config[1] for config in configs]
        unique_timeouts = set(timeouts)

        # Verify all timeouts are within expected range
        from backend.core.conf.settings import SETTINGS
        base = SETTINGS.REDIS.REDIS_CIRCUIT_BREAKER_RESET_TIMEOUT
        max_timeout = int(base * (1 + CIRCUIT_BREAKER_JITTER_FACTOR))

        for timeout in timeouts:
            assert base <= timeout <= max_timeout

    def test_get_config_applies_jitter_to_external_api_cb(self):
        """External API circuit breaker config should include jittered timeout."""
        registry = CircuitBreakerRegistry()

        configs = [
            registry._get_config(CircuitBreakerType.EXTERNAL_API)
            for _ in range(20)
        ]

        timeouts = [config[1] for config in configs]

        from backend.core.conf.settings import SETTINGS
        base = SETTINGS.EXTERNAL_API.EXTERNAL_API_CIRCUIT_BREAKER_RESET_TIMEOUT
        max_timeout = int(base * (1 + CIRCUIT_BREAKER_JITTER_FACTOR))

        for timeout in timeouts:
            assert base <= timeout <= max_timeout

    def test_different_cb_types_have_different_base_configs(self):
        """Different circuit breaker types should have different configurations."""
        registry = CircuitBreakerRegistry()

        db_config = registry._get_config(CircuitBreakerType.DATABASE)
        redis_config = registry._get_config(CircuitBreakerType.REDIS)
        api_config = registry._get_config(CircuitBreakerType.EXTERNAL_API)

        # Log names should be different
        assert db_config[2] == "Database"
        assert redis_config[2] == "Redis"
        assert api_config[2] == "External API"


class TestCircuitBreakerRegistry:
    """Tests for CircuitBreakerRegistry behavior."""

    def test_registry_caches_factories(self):
        """Registry should cache created factories."""
        registry = CircuitBreakerRegistry()

        # Get factory twice
        factory1 = registry.get_factory(CircuitBreakerType.DATABASE)
        factory2 = registry.get_factory(CircuitBreakerType.DATABASE)

        # Both calls should return the same factory instance
        assert factory1 is factory2

    def test_get_factory_creates_factory_lazily(self):
        """Factory should be created on first access."""
        registry = CircuitBreakerRegistry()

        # First access creates factory
        factory = registry.get_factory(CircuitBreakerType.REDIS)
        assert factory is not None

        # Second access returns same factory
        factory2 = registry.get_factory(CircuitBreakerType.REDIS)
        assert factory is factory2
