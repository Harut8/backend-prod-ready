"""
Pytest configuration and shared fixtures.

Provides fixtures for:
- Database sessions with automatic transaction rollback
- Mock Redis using fakeredis
- Common test utilities
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.infrastructure.cache.service import CacheService


# =============================================================================
# Database Fixtures
# =============================================================================


@pytest.fixture
def mock_async_session() -> AsyncMock:
    """Create a mock AsyncSession for unit tests."""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
async def test_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Create an in-memory SQLite database session for integration tests.

    Uses transaction rollback to ensure test isolation.
    """
    # Create in-memory SQLite engine for testing
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    # Create session factory
    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_factory() as session:
        async with session.begin():
            yield session
            # Rollback after test to ensure isolation
            await session.rollback()

    await engine.dispose()


@pytest.fixture
def mock_session_factory(mock_async_session: AsyncMock) -> MagicMock:
    """Create a mock session factory that returns the mock session."""
    factory = MagicMock()
    factory.create_session.return_value = mock_async_session
    return factory


# =============================================================================
# Redis/Cache Fixtures
# =============================================================================


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Create a mock Redis client for unit tests."""
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.setex = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=1)
    redis_mock.ping = AsyncMock(return_value=True)
    redis_mock.scan = AsyncMock(return_value=(0, []))
    redis_mock.close = AsyncMock()
    return redis_mock


@pytest.fixture
def mock_cache_service(mock_redis: AsyncMock) -> CacheService:
    """Create a CacheService with mocked Redis client."""
    service = CacheService()
    service._redis_client = mock_redis
    service._initialized = True
    return service


@pytest.fixture
async def fakeredis_cache_service() -> AsyncGenerator[CacheService, None]:
    """
    Create a CacheService with fakeredis for integration tests.

    This provides real Redis behavior without requiring a Redis server.
    """
    try:
        import fakeredis.aioredis
    except ImportError:
        pytest.skip("fakeredis not installed")

    service = CacheService()
    service._redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    service._initialized = True

    yield service

    if service._redis_client:
        await service._redis_client.close()


# =============================================================================
# HTTP/Request Fixtures
# =============================================================================


@pytest.fixture
def mock_request() -> MagicMock:
    """Create a mock FastAPI Request object."""
    request = MagicMock()
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    request.url = MagicMock()
    request.url.path = "/test"
    request.method = "GET"
    return request


@pytest.fixture
def mock_request_factory() -> Any:
    """Factory for creating mock requests with custom parameters."""

    def _create_request(
        client_ip: str = "127.0.0.1",
        x_forwarded_for: str | None = None,
        path: str = "/test",
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> MagicMock:
        request = MagicMock()
        request.headers = headers or {}
        if x_forwarded_for:
            request.headers["x-forwarded-for"] = x_forwarded_for
        request.client = MagicMock()
        request.client.host = client_ip
        request.url = MagicMock()
        request.url.path = path
        request.method = method
        return request

    return _create_request


# =============================================================================
# Unit of Work Fixtures
# =============================================================================


@pytest.fixture
def mock_uow() -> AsyncMock:
    """Create a mock Unit of Work for testing services."""
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()
    return uow


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend() -> str:
    """Configure anyio to use asyncio backend."""
    return "asyncio"
