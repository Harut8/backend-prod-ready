import asyncio
from collections.abc import Callable
from datetime import datetime
import fnmatch
import hashlib
import time
from typing import Any, TypeVar, cast

import orjson
import redis.asyncio as redis
import structlog

from backend.core.conf.settings import SETTINGS
from backend.core.security.circuit_breaker import (
    get_redis_circuit_breaker_factory,
    type_preserving_circuit_breaker,
)


logger = structlog.get_logger(__name__)


# =============================================================================
# In-Memory Cache (Fallback for local/dev)
# =============================================================================


class InMemoryCache:
    """Simple in-memory cache with TTL support.

    Used as fallback when Redis is unavailable in local/dev environments.
    NOT suitable for production with multiple workers/instances.
    """

    def __init__(self, max_size: int = 10000) -> None:
        self._cache: dict[str, tuple[Any, float | None]] = {}  # key -> (value, expiry_timestamp)
        self._max_size = max_size
        self._lock = asyncio.Lock()

    def _is_expired(self, expiry: float | None) -> bool:
        """Check if entry has expired."""
        if expiry is None:
            return False
        return time.time() > expiry

    async def _cleanup_expired(self) -> None:
        """Remove expired entries (called periodically)."""
        now = time.time()
        expired_keys = [k for k, (_, exp) in self._cache.items() if exp and now > exp]
        for key in expired_keys:
            self._cache.pop(key, None)

    async def get(self, key: str) -> str | None:
        """Get value from cache."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if self._is_expired(expiry):
                del self._cache[key]
                return None
            return cast("str | None", value)

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set value in cache with optional TTL."""
        async with self._lock:
            # Evict oldest entries if at capacity
            if len(self._cache) >= self._max_size:
                await self._cleanup_expired()
                # If still at capacity, remove 10% oldest entries
                if len(self._cache) >= self._max_size:
                    keys_to_remove = list(self._cache.keys())[: self._max_size // 10]
                    for k in keys_to_remove:
                        del self._cache[k]

            expiry = time.time() + ttl if ttl else None
            self._cache[key] = (value, expiry)
            return True

    async def setnx(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set value only if key doesn't exist."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                _, expiry = entry
                if not self._is_expired(expiry):
                    return False
            expiry = time.time() + ttl if ttl else None
            self._cache[key] = (value, expiry)
            return True

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching pattern."""
        async with self._lock:
            # Convert Redis pattern to fnmatch pattern
            fn_pattern = pattern.replace("*", "*")
            keys_to_delete = [k for k in self._cache if fnmatch.fnmatch(k, fn_pattern)]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)

    async def ping(self) -> bool:
        """Always returns True for in-memory cache."""
        return True

    async def close(self) -> None:
        """Clear the cache."""
        async with self._lock:
            self._cache.clear()


_metrics_unavailable_logged = False


def _record_cache_failure(operation: str) -> None:
    """Record cache operation failure metric (lazy import to avoid circular dependency)."""
    global _metrics_unavailable_logged  # noqa: PLW0603

    try:
        from backend.core.observability.metrics import record_cache_operation_failure  # noqa: PLC0415

        record_cache_operation_failure(operation)
    except ImportError:
        # Log once to avoid log spam, but make it visible that metrics are unavailable
        if not _metrics_unavailable_logged:
            logger.debug(
                "Metrics module unavailable - cache operation failures will not be recorded",
                operation=operation,
            )
            _metrics_unavailable_logged = True


T = TypeVar("T")

# Redis circuit breaker decorator - prevents cascading failures when Redis is slow/down
_redis_circuit_breaker = type_preserving_circuit_breaker(get_redis_circuit_breaker_factory()("redis_cache_operations"))


class CacheService:
    """Redis-based cache service with automatic connection management and error handling.

    Includes rate limiting for bulk deletion operations to prevent cache stampedes.
    Falls back to in-memory cache in local/dev environments when Redis is unavailable.
    """

    # Rate limiting for deletion operations to prevent abuse/stampede
    MAX_CONCURRENT_DELETIONS = 5

    # Environments where in-memory fallback is allowed
    _FALLBACK_ENVIRONMENTS = frozenset({"local", "dev", "test"})

    def __init__(self) -> None:
        self._redis_client: redis.Redis | None = None
        self._in_memory_cache: InMemoryCache | None = None
        self._initialized = False
        self._using_fallback = False
        self._deletion_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_DELETIONS)

    def _should_use_fallback(self) -> bool:
        """Check if in-memory fallback should be used."""
        return SETTINGS.APP.ENVIRONMENT in self._FALLBACK_ENVIRONMENTS

    async def _ensure_connected(self) -> None:
        """Ensure Redis connection is established."""
        if self._initialized:
            return

        try:
            self._redis_client = redis.from_url(  # type: ignore[no-untyped-call]
                SETTINGS.REDIS.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            # Test connection
            if self._redis_client:
                await self._redis_client.ping()
            logger.info("Redis cache service connected")
            self._initialized = True
        except (redis.ConnectionError, redis.TimeoutError) as e:
            self._redis_client = None
            if self._should_use_fallback():
                self._in_memory_cache = InMemoryCache()
                self._using_fallback = True
                logger.warning(
                    "Redis unavailable, using in-memory cache fallback",
                    error=str(e),
                    environment=SETTINGS.APP.ENVIRONMENT,
                )
            else:
                logger.warning(
                    "Redis connection failed, caching disabled",
                    error=str(e),
                    environment=SETTINGS.APP.ENVIRONMENT,
                )
            self._initialized = True
        except (ValueError, TypeError) as e:
            self._redis_client = None
            if self._should_use_fallback():
                self._in_memory_cache = InMemoryCache()
                self._using_fallback = True
                logger.warning(
                    "Redis config error, using in-memory cache fallback",
                    error=str(e),
                    environment=SETTINGS.APP.ENVIRONMENT,
                )
            else:
                logger.warning(
                    "Redis configuration error, caching disabled",
                    error=str(e),
                    environment=SETTINGS.APP.ENVIRONMENT,
                )
            self._initialized = True

    async def __aenter__(self) -> "CacheService":
        await self._ensure_connected()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Close Redis connection on context exit."""
        if self._redis_client:
            await self._redis_client.close()

    def _is_redis_available(self) -> bool:
        """Check if Redis client is available."""
        return self._redis_client is not None

    def _is_cache_available(self) -> bool:
        """Check if any cache backend (Redis or in-memory) is available."""
        return self._redis_client is not None or self._in_memory_cache is not None

    def _build_log_context(
        self,
        error: Exception,
        key: str | None = None,
        pattern: str | None = None,
    ) -> dict[str, str]:
        """Build log context for error logging."""
        _log_context = {"error": str(error)}
        if key:
            _log_context["key"] = key
        if pattern:
            _log_context["pattern"] = pattern
        return _log_context

    async def _execute_redis_operation(
        self,
        operation: Callable[[], Any],
        operation_name: str,
        key: str | None = None,
        pattern: str | None = None,
        default_return: T | None = None,
    ) -> T | None:
        """Execute Redis operation with circuit breaker and error handling.

        The circuit breaker prevents cascading latency when Redis becomes
        slow or unresponsive by failing fast after consecutive failures.
        """
        await self._ensure_connected()

        if not self._is_redis_available():
            return default_return

        @_redis_circuit_breaker
        async def _protected_operation() -> T | None:
            return cast("T | None", await operation())

        try:
            return await _protected_operation()  # type: ignore[no-any-return]
        except redis.RedisError as e:
            logger.warning("Failed to %s", operation_name, **self._build_log_context(e, key, pattern))
            _record_cache_failure(operation_name)
        except (TypeError, ValueError, orjson.JSONDecodeError) as e:
            logger.warning("Failed to %s", operation_name, **self._build_log_context(e, key, pattern))
            _record_cache_failure(operation_name)
        except Exception as e:
            # Handle CircuitBreakerFailed without importing it (to avoid mypy issues)
            if type(e).__name__ == "CircuitBreakerFailed":
                logger.warning(
                    "Redis circuit breaker open - failing fast",
                    operation=operation_name,
                    key=key,
                    pattern=pattern,
                )
                _record_cache_failure(operation_name)
            else:
                raise
        return default_return

    def hash_key(self, key: str) -> str:
        """Generate SHA256 hash of the key."""
        return hashlib.sha256(key.encode()).hexdigest()

    def generate_secure_key(self, prefix: str, *parts: str) -> str:
        """Generate a secure cache key with prefix and hashed parts."""
        _combined = ":".join(str(part) for part in parts if part)
        _hashed_parts = self.hash_key(_combined)
        return f"{prefix}:{_hashed_parts}"

    async def get(self, key: str) -> str | None:
        """Get string value from cache."""
        await self._ensure_connected()

        # Use in-memory fallback if active
        if self._using_fallback and self._in_memory_cache:
            result = await self._in_memory_cache.get(key)
            logger.debug("Cache lookup (in-memory)", key=key, hit=result is not None)
            return result

        async def _get_operation() -> str | None:
            _result = await self._redis_client.get(key)  # type: ignore[union-attr]
            logger.debug("Cache lookup", key=key, hit=_result is not None)
            return cast("str | None", _result)

        return await self._execute_redis_operation(
            _get_operation,
            "get cache value",
            key=key,
            default_return=None,
        )

    async def get_json(self, key: str) -> dict[str, Any] | None:
        """Get JSON value from cache with datetime deserialization."""
        await self._ensure_connected()

        # Use in-memory fallback if active
        if self._using_fallback and self._in_memory_cache:
            _value = await self._in_memory_cache.get(key)
            if _value is None:
                return None
            _json_value = orjson.loads(_value)
            if isinstance(_json_value.get("created_at"), str):
                _json_value["created_at"] = datetime.fromisoformat(_json_value["created_at"])
            if isinstance(_json_value.get("updated_at"), str):
                _json_value["updated_at"] = datetime.fromisoformat(_json_value["updated_at"])
            return cast("dict[str, Any]", _json_value)

        async def _get_json_operation() -> dict[str, Any] | None:
            _value = await self._redis_client.get(key)  # type: ignore[union-attr]
            if _value is None:
                return None

            _json_value = orjson.loads(_value)
            # Deserialize datetime fields
            if isinstance(_json_value.get("created_at"), str):
                _json_value["created_at"] = datetime.fromisoformat(_json_value["created_at"])
            if isinstance(_json_value.get("updated_at"), str):
                _json_value["updated_at"] = datetime.fromisoformat(_json_value["updated_at"])

            return cast("dict[str, Any]", _json_value)

        return await self._execute_redis_operation(
            _get_json_operation,
            "get JSON cache value",
            key=key,
            default_return=None,
        )

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set string value in cache with optional TTL."""
        await self._ensure_connected()

        # Use in-memory fallback if active
        if self._using_fallback and self._in_memory_cache:
            return await self._in_memory_cache.set(key, value, ttl)

        async def _set_operation() -> bool:
            if ttl:
                await self._redis_client.setex(key, ttl, value)  # type: ignore[union-attr]
            else:
                await self._redis_client.set(key, value)  # type: ignore[union-attr]
            return True

        return (
            await self._execute_redis_operation(
                _set_operation,
                "set cache value",
                key=key,
                default_return=False,
            )
            or False
        )

    async def setnx(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Atomically set value only if key does not exist (SET NX).

        This is critical for preventing race conditions in idempotency checks.
        Returns True if the key was set (acquired), False if key already existed.

        Args:
            key: Cache key
            value: Value to set
            ttl: Optional TTL in seconds

        Returns:
            True if key was set (lock acquired), False if key already existed
        """
        await self._ensure_connected()

        # Use in-memory fallback if active
        if self._using_fallback and self._in_memory_cache:
            return await self._in_memory_cache.setnx(key, value, ttl)

        async def _setnx_operation() -> bool:
            # Use SET with NX (only set if not exists) and optional EX (expiry)
            result = await self._redis_client.set(  # type: ignore[union-attr]
                key,
                value,
                nx=True,  # Only set if key does not exist
                ex=ttl,  # Expiry in seconds
            )
            # Redis returns True/OK if set, None if key already exists
            return result is not None

        return (
            await self._execute_redis_operation(
                _setnx_operation,
                "setnx cache value",
                key=key,
                default_return=False,
            )
            or False
        )

    async def set_json(self, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        """Set JSON value in cache with optional TTL."""
        await self._ensure_connected()

        # Use in-memory fallback if active
        if self._using_fallback and self._in_memory_cache:
            _json_value = orjson.dumps(value, default=str).decode("utf-8")
            return await self._in_memory_cache.set(key, _json_value, ttl)

        async def _set_json_operation() -> bool:
            _json_value = orjson.dumps(value, default=str)
            if ttl:
                await self._redis_client.setex(key, ttl, _json_value)  # type: ignore[union-attr]
            else:
                await self._redis_client.set(key, _json_value)  # type: ignore[union-attr]
            return True

        return (
            await self._execute_redis_operation(
                _set_json_operation,
                "set JSON cache value",
                key=key,
                default_return=False,
            )
            or False
        )

    async def delete(self, key: str) -> bool:
        """Delete a single key from cache."""
        await self._ensure_connected()

        # Use in-memory fallback if active
        if self._using_fallback and self._in_memory_cache:
            return await self._in_memory_cache.delete(key)

        async def _delete_operation() -> bool:
            _result = await self._redis_client.delete(key)  # type: ignore[union-attr]
            return bool(_result)

        return (
            await self._execute_redis_operation(
                _delete_operation,
                "delete cache value",
                key=key,
                default_return=False,
            )
            or False
        )

    async def delete_pattern(self, pattern: str, batch_size: int = 100, *, log_as_info: bool = False) -> int:
        """Delete all keys matching a pattern using cursor-based SCAN.

        Uses SCAN instead of KEYS to avoid blocking Redis server.
        KEYS is O(N) against all keys and blocks the server.
        SCAN is non-blocking and iterates incrementally.

        Rate limited via semaphore to prevent cache stampede from concurrent
        deletion requests.

        Args:
            pattern: Redis key pattern to match (e.g., "user:*")
            batch_size: Number of keys to scan per iteration
            log_as_info: If True, log deletions at INFO level; otherwise DEBUG level
        """
        await self._ensure_connected()

        _log_func = logger.info if log_as_info else logger.debug
        _log_message = "Invalidated cache entries" if log_as_info else "Deleted cache entries"

        # Use in-memory fallback if active
        if self._using_fallback and self._in_memory_cache:
            deleted = await self._in_memory_cache.delete_pattern(pattern)
            if deleted > 0:
                _log_func(_log_message, pattern=pattern, deleted_count=deleted)
            return deleted

        async def _delete_pattern_operation() -> int:
            _deleted = 0
            _cursor = 0
            while True:
                _cursor, _keys = await self._redis_client.scan(  # type: ignore[union-attr]
                    cursor=_cursor,
                    match=pattern,
                    count=batch_size,
                )
                if _keys:
                    _deleted += await self._redis_client.delete(*_keys)  # type: ignore[union-attr]
                if _cursor == 0:
                    break
            if _deleted > 0:
                _log_func(_log_message, pattern=pattern, deleted_count=_deleted)
            return _deleted

        # Rate limit deletion operations to prevent stampede
        async with self._deletion_semaphore:
            return (
                await self._execute_redis_operation(
                    _delete_pattern_operation,
                    "delete cache pattern",
                    pattern=pattern,
                    default_return=0,
                )
                or 0
            )

    async def invalidate_cache(self, pattern: str, batch_size: int = 100) -> int:
        """Invalidate cache entries matching a pattern.

        This is an alias for delete_pattern() with INFO-level logging,
        typically used for explicit cache invalidation operations.
        """
        return await self.delete_pattern(pattern, batch_size, log_as_info=True)

    async def ping(self) -> bool:
        """
        Check cache connectivity.

        Returns:
            True if cache (Redis or in-memory) is available and responding, False otherwise.
        """
        await self._ensure_connected()

        # In-memory fallback is always available
        if self._using_fallback and self._in_memory_cache:
            return await self._in_memory_cache.ping()

        if not self._is_redis_available():
            return False

        try:
            await self._redis_client.ping()  # type: ignore[union-attr]
        except (redis.RedisError, redis.ConnectionError, redis.TimeoutError):
            return False
        else:
            return True

    async def close(self) -> None:
        """Close cache connection gracefully."""
        # Close in-memory cache if active
        if self._in_memory_cache:
            await self._in_memory_cache.close()
            logger.info("In-memory cache cleared")
            self._in_memory_cache = None
            self._using_fallback = False

        # Close Redis if active
        if self._redis_client:
            try:
                await self._redis_client.close()
                # Disconnect connection pool to ensure all connections are closed
                if hasattr(self._redis_client, "connection_pool"):
                    await self._redis_client.connection_pool.disconnect()
                logger.info("Redis cache service connection closed")
            except (redis.ConnectionError, redis.TimeoutError, AttributeError) as e:
                logger.warning("Error closing Redis connection", error=str(e))
            finally:
                self._redis_client = None

        self._initialized = False
