from collections.abc import Callable
from datetime import datetime
import hashlib
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


def _record_cache_failure(operation: str) -> None:
    """Record cache operation failure metric (lazy import to avoid circular dependency)."""
    try:
        from backend.core.observability.metrics import record_cache_operation_failure  # noqa: PLC0415

        record_cache_operation_failure(operation)
    except ImportError:
        # Metrics module not available (e.g., during early initialization)
        pass


T = TypeVar("T")

# Redis circuit breaker decorator - prevents cascading failures when Redis is slow/down
_redis_circuit_breaker = type_preserving_circuit_breaker(get_redis_circuit_breaker_factory()("redis_cache_operations"))


class CacheService:
    """Redis-based cache service with automatic connection management and error handling."""

    def __init__(self) -> None:
        self._redis_client: redis.Redis | None = None
        self._initialized = False

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
            logger.warning("Redis connection failed, caching disabled", error=str(e))
            self._redis_client = None
            self._initialized = True
        except (ValueError, TypeError) as e:
            logger.warning("Redis configuration error, caching disabled", error=str(e))
            self._redis_client = None
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

        Args:
            pattern: Redis key pattern to match (e.g., "user:*")
            batch_size: Number of keys to scan per iteration
            log_as_info: If True, log deletions at INFO level; otherwise DEBUG level
        """
        _log_func = logger.info if log_as_info else logger.debug
        _log_message = "Invalidated cache entries" if log_as_info else "Deleted cache entries"

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
        Check Redis connectivity via PING command.

        Returns:
            True if Redis is connected and responding, False otherwise.
        """
        await self._ensure_connected()

        if not self._is_redis_available():
            return False

        try:
            await self._redis_client.ping()  # type: ignore[union-attr]
        except (redis.RedisError, redis.ConnectionError, redis.TimeoutError):
            return False
        else:
            return True

    async def close(self) -> None:
        """Close Redis connection gracefully."""
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
