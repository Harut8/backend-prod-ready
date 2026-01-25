"""
Idempotency service for preventing duplicate webhook/API processing.

This service ensures that webhooks from Paddle, Telegram, Stripe, etc.
are processed exactly once, even if they are delivered multiple times.
"""

from dataclasses import dataclass
from enum import Enum
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

    Usage:
        async with IdempotencyService(cache) as idempotency:
            result = await idempotency.check("paddle_webhook:evt_123")
            if result.is_duplicate:
                return result.cached_response
            if result.is_in_progress:
                return {"status": "processing"}

            # Process the webhook...
            response = await process_webhook(data)

            # Store the result
            await idempotency.store("paddle_webhook:evt_123", response)
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

    def _build_key(self, key: str) -> str:
        """Build the full cache key with prefix."""
        return f"{self.CACHE_PREFIX}:{key}"

    def _build_in_progress_key(self, key: str) -> str:
        """Build the in-progress marker key."""
        return f"{self.CACHE_PREFIX}:{key}{self.IN_PROGRESS_SUFFIX}"

    async def check(self, key: str) -> IdempotencyResult:
        """
        Check if an operation was already processed.

        Args:
            key: Unique identifier for the operation (e.g., "paddle_webhook:evt_123")

        Returns:
            IdempotencyResult with status and cached response if available
        """
        _full_key = self._build_key(key)
        _in_progress_key = self._build_in_progress_key(key)

        # Check if already completed
        _cached = await self._cache.get(_full_key)
        if _cached is not None:
            try:
                _response = orjson.loads(_cached)
                logger.debug("Idempotency hit - returning cached response", key=key)
                return IdempotencyResult(
                    status=IdempotencyStatus.DUPLICATE,
                    cached_response=_response,
                )
            except orjson.JSONDecodeError:
                logger.warning("Failed to decode cached idempotency response", key=key)
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
            logger.debug("Idempotency check - new operation (lock acquired)", key=key)
            return IdempotencyResult(status=IdempotencyStatus.NEW)

        # Another worker already has the lock
        logger.debug("Idempotency in progress by another worker", key=key)
        return IdempotencyResult(status=IdempotencyStatus.IN_PROGRESS)

    async def store(
        self,
        key: str,
        response: Any,
        ttl: int | None = None,
    ) -> None:
        """
        Store the result of a successful operation.

        Args:
            key: Same key used in check()
            response: The response to cache (will be JSON serialized)
            ttl: Time-to-live in seconds (default: 7 days)
        """
        _full_key = self._build_key(key)
        _in_progress_key = self._build_in_progress_key(key)
        _ttl = ttl or self.DEFAULT_TTL

        try:
            _serialized = orjson.dumps(response).decode("utf-8")
            await self._cache.set(_full_key, _serialized, ttl=_ttl)
            logger.debug("Idempotency response stored", key=key, ttl=_ttl)
        except (orjson.JSONEncodeError, TypeError) as e:
            logger.warning("Failed to serialize idempotency response", key=key, error=str(e))

        # Remove in-progress marker
        await self._cache.delete(_in_progress_key)

    async def clear_in_progress(self, key: str) -> None:
        """
        Clear the in-progress marker without storing a response.

        Use this when processing fails and you want to allow retry.
        """
        _in_progress_key = self._build_in_progress_key(key)
        await self._cache.delete(_in_progress_key)
        logger.debug("Idempotency in-progress marker cleared", key=key)

    async def invalidate(self, key: str) -> None:
        """
        Invalidate a stored idempotency result.

        Use this if you need to reprocess a previously completed operation.
        """
        _full_key = self._build_key(key)
        _in_progress_key = self._build_in_progress_key(key)
        await self._cache.delete(_full_key)
        await self._cache.delete(_in_progress_key)
        logger.debug("Idempotency result invalidated", key=key)
