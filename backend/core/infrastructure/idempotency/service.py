"""
Idempotency service for preventing duplicate webhook/API processing.

This service ensures that webhooks from Paddle, Telegram, Stripe, etc.
are processed exactly once, even if they are delivered multiple times.

Security considerations:
- External idempotency keys are hashed to prevent collision attacks
- Namespace support prevents cross-tenant key collisions
- Keys are prefixed to isolate idempotency data from other cache entries
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any

import orjson
import structlog

from backend.core.infrastructure.cache.service import CacheService


logger = structlog.get_logger(__name__)


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

        Args:
            key: Unique identifier for the operation (e.g., "evt_123")
            namespace: Optional namespace for isolation (e.g., "paddle", "stripe", "telegram")

        Returns:
            IdempotencyResult with status and cached response if available
        """
        _full_key = self._build_key(key, namespace)
        _in_progress_key = self._build_in_progress_key(key, namespace)

        # Check if already completed
        _cached = await self._cache.get(_full_key)
        if _cached is not None:
            try:
                _response = orjson.loads(_cached)
                logger.debug("Idempotency hit - returning cached response", key=key, namespace=namespace)
                return IdempotencyResult(
                    status=IdempotencyStatus.DUPLICATE,
                    cached_response=_response,
                )
            except orjson.JSONDecodeError:
                logger.warning("Failed to decode cached idempotency response", key=key, namespace=namespace)
                # Fall through to treat as new

        # Atomically try to acquire in-progress marker using SETNX
        # This prevents race conditions where two concurrent requests both pass
        # a sequential check-then-set pattern
        _acquired = await self._cache.setnx(
            _in_progress_key,
            "1",
            ttl=self.IN_PROGRESS_TTL,
        )

        if _acquired:
            logger.debug("Idempotency check - new operation (lock acquired)", key=key, namespace=namespace)
            return IdempotencyResult(status=IdempotencyStatus.NEW)

        # Another worker already has the lock
        logger.debug("Idempotency in progress by another worker", key=key, namespace=namespace)
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
