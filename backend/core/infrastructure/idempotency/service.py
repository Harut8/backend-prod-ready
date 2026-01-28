"""
Idempotency service for preventing duplicate webhook/API processing.

This service ensures that webhooks from Paddle, Telegram, Stripe, etc.
are processed exactly once, even if they are delivered multiple times.

Security considerations:
- External idempotency keys are hashed to prevent collision attacks
- Namespace support prevents cross-tenant key collisions
- Keys are prefixed to isolate idempotency data from other cache entries

Race condition prevention:
- Uses atomic Lua script for check-and-acquire to prevent TOCTOU races
- Falls back to non-atomic check (with warning) if Redis unavailable
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any

import orjson
import structlog

from backend.core.infrastructure.cache.service import CacheService


logger = structlog.get_logger(__name__)


class IdempotencyCacheError(Exception):
    """Raised when idempotency check fails due to cache unavailability.

    This exception indicates that the idempotency service cannot function
    properly because the cache (Redis) is unavailable. Callers should
    handle this by failing the request rather than proceeding without
    idempotency protection (which could lead to duplicate processing).
    """

    pass


# Lua script for atomic idempotency check-and-acquire
# This prevents race conditions where two concurrent requests both pass
# a sequential check-then-set pattern
#
# KEYS[1] = completed_key (stores the result after processing)
# KEYS[2] = in_progress_key (lock to prevent concurrent processing)
# ARGV[1] = in_progress_ttl (seconds)
#
# Returns: {status, cached_response}
# - {"NEW", nil} - First request, proceed with processing
# - {"DUPLICATE", cached_data} - Already completed, return cached
# - {"IN_PROGRESS", nil} - Another worker is processing
IDEMPOTENCY_CHECK_LUA_SCRIPT = """
local completed_key = KEYS[1]
local in_progress_key = KEYS[2]
local in_progress_ttl = tonumber(ARGV[1])

-- Check if already completed (most common case for duplicates)
local cached = redis.call('GET', completed_key)
if cached then
    return {'DUPLICATE', cached}
end

-- Atomically try to acquire in-progress lock using SET NX EX
-- This is the critical section that prevents race conditions
local acquired = redis.call('SET', in_progress_key, '1', 'NX', 'EX', in_progress_ttl)
if acquired then
    return {'NEW', ''}
else
    return {'IN_PROGRESS', ''}
end
"""


class IdempotencyStatus(str, Enum):
    """Status of an idempotency check."""

    NEW = "new"  # First time seeing this key - proceed with processing
    DUPLICATE = "duplicate"  # Already processed - return cached result
    IN_PROGRESS = "in_progress"  # Currently being processed by another worker


@dataclass
class IdempotencyResult:
    """Result of an idempotency check."""

    status: IdempotencyStatus
    cached_response: Any | None = None

    @property
    def is_new(self) -> bool:
        """Check if this is a new request that should be processed."""
        return self.status == IdempotencyStatus.NEW

    @property
    def is_duplicate(self) -> bool:
        """Check if this request was already processed."""
        return self.status == IdempotencyStatus.DUPLICATE

    @property
    def is_in_progress(self) -> bool:
        """Check if this request is currently being processed."""
        return self.status == IdempotencyStatus.IN_PROGRESS


class IdempotencyService:
    """
    Service for ensuring idempotent webhook and API processing.

    Uses Redis to track processed requests and cache responses.
    Supports concurrent processing with in-progress markers.

    Security:
    - External keys are SHA256 hashed to prevent collision attacks
    - Namespace support isolates keys by tenant/source
    - Predictable key patterns cannot be exploited

    Usage:
        async with IdempotencyService(cache) as idempotency:
            # With namespace for multi-tenant isolation
            result = await idempotency.check("evt_123", namespace="paddle")
            if result.is_duplicate:
                return result.cached_response
            if result.is_in_progress:
                return {"status": "processing"}

            # Process the webhook...
            response = await process_webhook(data)

            # Store the result
            await idempotency.store("evt_123", response, namespace="paddle")
            return response
    """

    CACHE_PREFIX = "idempotency"
    IN_PROGRESS_SUFFIX = ":in_progress"
    DEFAULT_TTL = 86400 * 7  # 7 days
    IN_PROGRESS_TTL = 300  # 5 minutes (timeout for stuck processing)

    def __init__(self, cache_service: CacheService) -> None:
        self._cache = cache_service

    async def __aenter__(self) -> "IdempotencyService":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    @staticmethod
    def _hash_key(key: str) -> str:
        """Hash the key using SHA256 to prevent collision attacks.

        External idempotency keys (e.g., webhook event IDs) could be crafted
        by attackers to collide with legitimate requests. Hashing ensures
        unpredictable internal keys.
        """
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _build_key(self, key: str, namespace: str | None = None) -> str:
        """Build the full cache key with prefix, optional namespace, and hash.

        Args:
            key: The external idempotency key (will be hashed)
            namespace: Optional namespace for multi-tenant isolation (e.g., "paddle", "stripe")
        """
        _hashed = self._hash_key(key)
        if namespace:
            return f"{self.CACHE_PREFIX}:{namespace}:{_hashed}"
        return f"{self.CACHE_PREFIX}:{_hashed}"

    def _build_in_progress_key(self, key: str, namespace: str | None = None) -> str:
        """Build the in-progress marker key."""
        _base_key = self._build_key(key, namespace)
        return f"{_base_key}{self.IN_PROGRESS_SUFFIX}"

    async def check(self, key: str, *, namespace: str | None = None) -> IdempotencyResult:
        """
        Check if an operation was already processed.

        Uses an atomic Lua script to prevent TOCTOU race conditions where
        two concurrent requests both pass a sequential check-then-set pattern.

        Args:
            key: Unique identifier for the operation (e.g., "evt_123")
            namespace: Optional namespace for isolation (e.g., "paddle", "stripe", "telegram")

        Returns:
            IdempotencyResult with status and cached response if available
        """
        _full_key = self._build_key(key, namespace)
        _in_progress_key = self._build_in_progress_key(key, namespace)

        # Try atomic check-and-acquire using Lua script (preferred)
        _lua_result = await self._cache.execute_lua_script(
            IDEMPOTENCY_CHECK_LUA_SCRIPT,
            keys=[_full_key, _in_progress_key],
            args=[self.IN_PROGRESS_TTL],
        )

        if _lua_result is not None:
            # Lua script executed successfully
            _status = _lua_result[0]
            _cached_data = _lua_result[1] if len(_lua_result) > 1 else None

            if _status == "DUPLICATE":
                if _cached_data:
                    try:
                        _response = orjson.loads(_cached_data)
                        logger.debug("Idempotency hit - returning cached response", key=key, namespace=namespace)
                        return IdempotencyResult(
                            status=IdempotencyStatus.DUPLICATE,
                            cached_response=_response,
                        )
                    except orjson.JSONDecodeError:
                        logger.warning("Failed to decode cached idempotency response", key=key, namespace=namespace)
                        # Fall through to treat as new via non-atomic path
                else:
                    # Duplicate but no cached data (shouldn't happen, but handle gracefully)
                    logger.debug("Idempotency duplicate without cached data", key=key, namespace=namespace)
                    return IdempotencyResult(status=IdempotencyStatus.DUPLICATE, cached_response=None)

            elif _status == "NEW":
                logger.debug("Idempotency check - new operation (lock acquired atomically)", key=key, namespace=namespace)
                return IdempotencyResult(status=IdempotencyStatus.NEW)

            elif _status == "IN_PROGRESS":
                logger.debug("Idempotency in progress by another worker", key=key, namespace=namespace)
                return IdempotencyResult(status=IdempotencyStatus.IN_PROGRESS)

        # Lua script returned None - either Redis unavailable or in fallback mode
        # Check if we have in-memory fallback available
        if self._cache._using_fallback and self._cache._in_memory_cache:
            # Fallback mode with in-memory cache - use non-atomic check
            # This has a small race window but is acceptable for development/testing
            logger.warning(
                "Using non-atomic idempotency check (in-memory fallback mode)",
                key=key,
                namespace=namespace,
            )
            return await self._check_non_atomic(key, namespace=namespace)

        # No Redis and no in-memory fallback - fail closed to prevent duplicate processing
        # This is critical for production where idempotency is a requirement
        if not await self._cache.ping():
            logger.error(
                "Idempotency check failed - cache unavailable (fail-closed)",
                key=key,
                namespace=namespace,
            )
            raise IdempotencyCacheError(
                f"Cache unavailable for idempotency check (key={key}, namespace={namespace}). "
                "Cannot proceed without idempotency protection."
            )

        # Cache is available but Lua script failed for unknown reason
        # Fall back to non-atomic check with warning
        logger.warning(
            "Using non-atomic idempotency check (Lua script failed unexpectedly)",
            key=key,
            namespace=namespace,
        )
        return await self._check_non_atomic(key, namespace=namespace)

    async def _check_non_atomic(self, key: str, *, namespace: str | None = None) -> IdempotencyResult:
        """
        Non-atomic fallback for idempotency check.

        WARNING: This has a small race condition window between checking
        the completed key and acquiring the in-progress lock. Use only
        when Lua scripts are unavailable (e.g., in-memory fallback mode).
        """
        _full_key = self._build_key(key, namespace)
        _in_progress_key = self._build_in_progress_key(key, namespace)

        # Check if already completed
        _cached = await self._cache.get(_full_key)
        if _cached is not None:
            try:
                _response = orjson.loads(_cached)
                return IdempotencyResult(
                    status=IdempotencyStatus.DUPLICATE,
                    cached_response=_response,
                )
            except orjson.JSONDecodeError:
                logger.warning("Failed to decode cached idempotency response", key=key, namespace=namespace)

        # Try to acquire in-progress marker
        _acquired = await self._cache.setnx(
            _in_progress_key,
            "1",
            ttl=self.IN_PROGRESS_TTL,
        )

        if _acquired:
            return IdempotencyResult(status=IdempotencyStatus.NEW)

        return IdempotencyResult(status=IdempotencyStatus.IN_PROGRESS)

    async def store(
        self,
        key: str,
        response: Any,
        ttl: int | None = None,
        *,
        namespace: str | None = None,
    ) -> None:
        """
        Store the result of a successful operation.

        Args:
            key: Same key used in check()
            response: The response to cache (will be JSON serialized)
            ttl: Time-to-live in seconds (default: 7 days)
            namespace: Same namespace used in check()
        """
        _full_key = self._build_key(key, namespace)
        _in_progress_key = self._build_in_progress_key(key, namespace)
        _ttl = ttl or self.DEFAULT_TTL

        try:
            _serialized = orjson.dumps(response).decode("utf-8")
            await self._cache.set(_full_key, _serialized, ttl=_ttl)
            logger.debug("Idempotency response stored", key=key, namespace=namespace, ttl=_ttl)
        except (orjson.JSONEncodeError, TypeError) as e:
            logger.warning("Failed to serialize idempotency response", key=key, namespace=namespace, error=str(e))

        # Remove in-progress marker
        await self._cache.delete(_in_progress_key)

    async def clear_in_progress(self, key: str, *, namespace: str | None = None) -> None:
        """
        Clear the in-progress marker without storing a response.

        Use this when processing fails and you want to allow retry.

        Args:
            key: Same key used in check()
            namespace: Same namespace used in check()
        """
        _in_progress_key = self._build_in_progress_key(key, namespace)
        await self._cache.delete(_in_progress_key)
        logger.debug("Idempotency in-progress marker cleared", key=key, namespace=namespace)

    async def invalidate(self, key: str, *, namespace: str | None = None) -> None:
        """
        Invalidate a stored idempotency result.

        Use this if you need to reprocess a previously completed operation.

        Args:
            key: Same key used in check()
            namespace: Same namespace used in check()
        """
        _full_key = self._build_key(key, namespace)
        _in_progress_key = self._build_in_progress_key(key, namespace)
        await self._cache.delete(_full_key)
        await self._cache.delete(_in_progress_key)
        logger.debug("Idempotency result invalidated", key=key, namespace=namespace)

    async def get_stuck_in_progress_count(self, namespace: str | None = None) -> int:
        """
        Get count of potentially stuck in-progress operations.

        This is useful for monitoring and alerting on operations that may have
        failed without proper cleanup. Operations stuck in IN_PROGRESS state
        for longer than IN_PROGRESS_TTL (5 minutes) indicate a problem.

        Args:
            namespace: Optional namespace to filter by

        Returns:
            Count of in-progress markers (approximate, using SCAN)

        Note:
            This is an expensive operation on large Redis instances.
            Use sparingly, typically from health check or monitoring endpoints.
        """
        if self._cache._using_fallback or not self._cache._is_redis_available():
            # Can't scan in-memory cache efficiently, return -1 to indicate unknown
            return -1

        pattern = f"{self.CACHE_PREFIX}:{namespace}:*{self.IN_PROGRESS_SUFFIX}" if namespace else f"{self.CACHE_PREFIX}:*{self.IN_PROGRESS_SUFFIX}"

        # Use SCAN to count in-progress keys without blocking
        count = 0
        cursor = 0

        try:
            while True:
                cursor, keys = await self._cache._redis_client.scan(  # type: ignore[union-attr]
                    cursor=cursor,
                    match=pattern,
                    count=100,
                )
                count += len(keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning("Failed to scan for stuck in-progress markers", error=str(e))
            return -1

        if count > 0:
            logger.info(
                "Found in-progress idempotency markers",
                count=count,
                namespace=namespace,
                pattern=pattern,
            )

        return count

    async def cleanup_stuck_in_progress(
        self,
        namespace: str | None = None,
        *,
        max_cleanup: int = 100,
    ) -> int:
        """
        Clean up stuck in-progress markers.

        This is a manual recovery operation for when workers crash without
        cleaning up their in-progress markers. Should be used carefully as
        it could allow duplicate processing if called while processing is
        actually still in progress.

        Args:
            namespace: Optional namespace to filter by
            max_cleanup: Maximum number of markers to clean up (safety limit)

        Returns:
            Number of markers cleaned up
        """
        if self._cache._using_fallback or not self._cache._is_redis_available():
            logger.warning("Cannot cleanup stuck markers - Redis unavailable")
            return 0

        pattern = f"{self.CACHE_PREFIX}:{namespace}:*{self.IN_PROGRESS_SUFFIX}" if namespace else f"{self.CACHE_PREFIX}:*{self.IN_PROGRESS_SUFFIX}"

        cleaned = 0
        cursor = 0

        try:
            while cleaned < max_cleanup:
                cursor, keys = await self._cache._redis_client.scan(  # type: ignore[union-attr]
                    cursor=cursor,
                    match=pattern,
                    count=100,
                )
                for key in keys:
                    if cleaned >= max_cleanup:
                        break
                    await self._cache._redis_client.delete(key)  # type: ignore[union-attr]
                    cleaned += 1

                if cursor == 0:
                    break
        except Exception as e:
            logger.warning("Failed during stuck marker cleanup", error=str(e), cleaned=cleaned)

        if cleaned > 0:
            logger.warning(
                "Cleaned up stuck in-progress markers",
                count=cleaned,
                namespace=namespace,
            )

        return cleaned
