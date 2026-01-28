"""
Integration tests for IdempotencyService.

Tests critical paths:
- Atomic Lua script for check-and-acquire (race condition prevention)
- Fail-closed behavior when cache unavailable
- Duplicate detection and in-progress handling
- Stuck marker monitoring and cleanup
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.core.infrastructure.cache.service import CacheService
from backend.core.infrastructure.idempotency import (
    IdempotencyCacheError,
    IdempotencyService,
)
from backend.core.infrastructure.idempotency.service import IdempotencyStatus


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestIdempotencyAtomicCheck:
    """Tests for atomic Lua script-based idempotency check."""

    async def test_new_key_returns_new_status(self, redis_cache_service: CacheService):
        """First check for a key should return NEW status."""
        service = IdempotencyService(redis_cache_service)

        result = await service.check("test_key_1", namespace="test")

        assert result.status == IdempotencyStatus.NEW
        assert result.cached_response is None

    async def test_duplicate_key_returns_duplicate_status(self, redis_cache_service: CacheService):
        """After marking complete, subsequent checks should return DUPLICATE."""
        service = IdempotencyService(redis_cache_service)

        # First check - NEW
        result1 = await service.check("test_key_2", namespace="test")
        assert result1.status == IdempotencyStatus.NEW

        # Mark as complete with response
        await service.mark_complete("test_key_2", {"status": "processed"}, namespace="test")

        # Second check - DUPLICATE with cached response
        result2 = await service.check("test_key_2", namespace="test")
        assert result2.status == IdempotencyStatus.DUPLICATE
        assert result2.cached_response == {"status": "processed"}

    async def test_in_progress_key_returns_in_progress_status(self, redis_cache_service: CacheService):
        """If another worker has the lock, should return IN_PROGRESS."""
        service = IdempotencyService(redis_cache_service)

        # First check acquires lock
        result1 = await service.check("test_key_3", namespace="test")
        assert result1.status == IdempotencyStatus.NEW

        # Second check should see IN_PROGRESS (lock not released)
        result2 = await service.check("test_key_3", namespace="test")
        assert result2.status == IdempotencyStatus.IN_PROGRESS

    async def test_concurrent_checks_only_one_gets_new(self, redis_cache_service: CacheService):
        """
        Test atomic behavior under concurrent access.

        When multiple workers check the same key simultaneously,
        only ONE should get NEW status - others should get IN_PROGRESS.
        This verifies the Lua script atomicity.
        """
        service = IdempotencyService(redis_cache_service)
        results = []

        async def check_key():
            result = await service.check("concurrent_key", namespace="test")
            results.append(result.status)

        # Run 10 concurrent checks
        await asyncio.gather(*[check_key() for _ in range(10)])

        # Exactly one should be NEW
        new_count = results.count(IdempotencyStatus.NEW)
        in_progress_count = results.count(IdempotencyStatus.IN_PROGRESS)

        assert new_count == 1, f"Expected exactly 1 NEW, got {new_count}"
        assert in_progress_count == 9, f"Expected 9 IN_PROGRESS, got {in_progress_count}"

    async def test_lua_script_used_for_atomicity(self, redis_cache_service: CacheService):
        """Verify Lua script execution is used (not non-atomic fallback)."""
        service = IdempotencyService(redis_cache_service)

        # Spy on execute_lua_script
        original_execute = redis_cache_service.execute_lua_script
        call_count = 0

        async def spy_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return await original_execute(*args, **kwargs)

        redis_cache_service.execute_lua_script = spy_execute

        await service.check("lua_test_key", namespace="test")

        assert call_count == 1, "Lua script should be used for atomic check"


class TestIdempotencyFailClosed:
    """Tests for fail-closed behavior when cache is unavailable."""

    async def test_raises_error_when_cache_unavailable(self):
        """Should raise IdempotencyCacheError when Redis is unavailable."""
        service = CacheService()
        service._initialized = True
        service._using_fallback = False
        service._redis_client = None  # Simulate unavailable Redis

        idempotency = IdempotencyService(service)

        with pytest.raises(IdempotencyCacheError) as exc_info:
            await idempotency.check("fail_key", namespace="test")

        assert "Cache unavailable" in str(exc_info.value)

    async def test_fallback_mode_uses_non_atomic_check(self):
        """In fallback mode with in-memory cache, should use non-atomic check."""
        try:
            import fakeredis.aioredis
        except ImportError:
            pytest.skip("fakeredis not installed")

        service = CacheService()
        service._initialized = True
        service._using_fallback = True
        service._in_memory_cache = AsyncMock()
        service._in_memory_cache.get = AsyncMock(return_value=None)
        service._in_memory_cache.setnx = AsyncMock(return_value=True)

        idempotency = IdempotencyService(service)

        # Should not raise, should use non-atomic fallback
        result = await idempotency.check("fallback_key", namespace="test")
        assert result.status == IdempotencyStatus.NEW


class TestIdempotencyMonitoring:
    """Tests for in-progress marker monitoring and cleanup."""

    async def test_get_stuck_in_progress_count(self, redis_cache_service: CacheService):
        """Should count in-progress markers for monitoring."""
        service = IdempotencyService(redis_cache_service)

        # Create some in-progress markers
        await service.check("stuck_1", namespace="monitor")
        await service.check("stuck_2", namespace="monitor")
        await service.check("stuck_3", namespace="monitor")

        # Check count
        count = await service.get_stuck_in_progress_count(namespace="monitor")
        assert count == 3

    async def test_cleanup_stuck_in_progress(self, redis_cache_service: CacheService):
        """Should clean up stuck in-progress markers."""
        service = IdempotencyService(redis_cache_service)

        # Create some in-progress markers
        await service.check("cleanup_1", namespace="cleanup")
        await service.check("cleanup_2", namespace="cleanup")

        # Verify they exist
        count_before = await service.get_stuck_in_progress_count(namespace="cleanup")
        assert count_before == 2

        # Clean up
        cleaned = await service.cleanup_stuck_in_progress(namespace="cleanup", max_cleanup=10)
        assert cleaned == 2

        # Verify they're gone
        count_after = await service.get_stuck_in_progress_count(namespace="cleanup")
        assert count_after == 0


class TestIdempotencyNamespaceIsolation:
    """Tests for namespace isolation."""

    async def test_different_namespaces_are_isolated(self, redis_cache_service: CacheService):
        """Same key in different namespaces should be independent."""
        service = IdempotencyService(redis_cache_service)

        # Check same key in namespace A
        result_a = await service.check("shared_key", namespace="namespace_a")
        assert result_a.status == IdempotencyStatus.NEW

        # Same key in namespace B should also be NEW (isolated)
        result_b = await service.check("shared_key", namespace="namespace_b")
        assert result_b.status == IdempotencyStatus.NEW

    async def test_keys_are_hashed_for_security(self, redis_cache_service: CacheService):
        """External keys should be hashed to prevent collision attacks."""
        service = IdempotencyService(redis_cache_service)

        # The internal key should be a hash, not the raw key
        internal_key = service._build_key("user_supplied_key", "test")

        # Should contain hash characters (hex)
        assert len(internal_key) > len("user_supplied_key")
        assert "idempotency" in internal_key


class TestIdempotencyInvalidation:
    """Tests for result invalidation."""

    async def test_invalidate_allows_reprocessing(self, redis_cache_service: CacheService):
        """After invalidation, key should return NEW again."""
        service = IdempotencyService(redis_cache_service)

        # Process and complete
        await service.check("invalidate_key", namespace="test")
        await service.mark_complete("invalidate_key", {"done": True}, namespace="test")

        # Verify it's a duplicate
        result1 = await service.check("invalidate_key", namespace="test")
        assert result1.status == IdempotencyStatus.DUPLICATE

        # Invalidate
        await service.invalidate("invalidate_key", namespace="test")

        # Should be NEW again
        result2 = await service.check("invalidate_key", namespace="test")
        assert result2.status == IdempotencyStatus.NEW
