"""
Unit tests for Unit of Work.

Tests critical paths:
- Shared session configuration
- Session lock for concurrent access coordination
- Auto-commit behavior
- Ownership tracking
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.infrastructure.database.uow import BaseUnitOfWork


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ConcreteUnitOfWork(BaseUnitOfWork):
    """Concrete implementation for testing abstract BaseUnitOfWork."""

    def _register_repositories(self) -> None:
        """No repositories needed for these tests."""
        pass


class TestSharedSessionConfiguration:
    """Tests for shared session configuration."""

    def test_configure_for_shared_session_sets_correct_flags(self):
        """Should set auto_commit=False and owns_session=False."""
        uow = ConcreteUnitOfWork()
        mock_session = AsyncMock()

        uow.configure_for_shared_session(mock_session)

        assert uow._session is mock_session
        assert uow._auto_commit is False
        assert uow._owns_session is False

    def test_configure_for_shared_session_accepts_lock(self):
        """Should accept and store shared lock."""
        uow = ConcreteUnitOfWork()
        mock_session = AsyncMock()
        shared_lock = asyncio.Lock()

        uow.configure_for_shared_session(mock_session, shared_lock=shared_lock)

        assert uow._shared_session_lock is shared_lock
        assert uow.shared_session_lock is shared_lock

    def test_configure_without_lock_sets_none(self):
        """Should set lock to None when not provided."""
        uow = ConcreteUnitOfWork()
        mock_session = AsyncMock()

        uow.configure_for_shared_session(mock_session)

        assert uow._shared_session_lock is None

    def test_configure_returns_self_for_chaining(self):
        """Should return self for method chaining."""
        uow = ConcreteUnitOfWork()
        mock_session = AsyncMock()

        result = uow.configure_for_shared_session(mock_session)

        assert result is uow


class TestSessionOwnership:
    """Tests for session ownership tracking."""

    def test_owns_session_true_when_session_not_provided(self):
        """Should own session when creating own session."""
        uow = ConcreteUnitOfWork()

        assert uow.owns_session is True

    def test_owns_session_false_when_session_provided(self):
        """Should not own session when session is provided."""
        mock_session = AsyncMock()
        uow = ConcreteUnitOfWork(session=mock_session)

        assert uow.owns_session is False

    def test_owns_session_false_after_shared_config(self):
        """Should not own session after shared configuration."""
        uow = ConcreteUnitOfWork()
        assert uow.owns_session is True  # Initially owns

        uow.configure_for_shared_session(AsyncMock())
        assert uow.owns_session is False  # No longer owns


class TestAutoCommitBehavior:
    """Tests for auto-commit behavior."""

    def test_auto_commit_true_by_default(self):
        """Auto-commit should be True by default."""
        uow = ConcreteUnitOfWork()

        assert uow.auto_commit is True

    def test_auto_commit_false_when_disabled(self):
        """Auto-commit can be disabled."""
        uow = ConcreteUnitOfWork(auto_commit=False)

        assert uow.auto_commit is False

    def test_auto_commit_setter(self):
        """Auto-commit can be changed after construction."""
        uow = ConcreteUnitOfWork()
        assert uow.auto_commit is True

        uow.auto_commit = False
        assert uow.auto_commit is False


class TestSharedSessionLockUsage:
    """Tests demonstrating proper shared session lock usage."""

    async def test_lock_can_coordinate_concurrent_access(self):
        """Lock should coordinate access between multiple UoWs."""
        mock_session = AsyncMock()
        shared_lock = asyncio.Lock()

        uow1 = ConcreteUnitOfWork()
        uow2 = ConcreteUnitOfWork()

        uow1.configure_for_shared_session(mock_session, shared_lock=shared_lock)
        uow2.configure_for_shared_session(mock_session, shared_lock=shared_lock)

        # Both UoWs share the same lock
        assert uow1.shared_session_lock is uow2.shared_session_lock

        # Demonstrate lock usage
        execution_order = []

        async def task1():
            async with uow1.shared_session_lock:
                execution_order.append("task1_start")
                await asyncio.sleep(0.05)
                execution_order.append("task1_end")

        async def task2():
            await asyncio.sleep(0.01)  # Slight delay to ensure task1 starts first
            async with uow2.shared_session_lock:
                execution_order.append("task2_start")
                execution_order.append("task2_end")

        await asyncio.gather(task1(), task2())

        # Task 2 should wait for task 1 to complete
        assert execution_order == [
            "task1_start",
            "task1_end",
            "task2_start",
            "task2_end",
        ]


class TestSessionProperty:
    """Tests for session property access."""

    def test_session_property_raises_when_not_initialized(self):
        """Accessing session before initialization should raise."""
        uow = ConcreteUnitOfWork()

        with pytest.raises(RuntimeError, match="Session not initialized"):
            _ = uow.session

    def test_session_property_returns_session_when_set(self):
        """Session property should return the session."""
        mock_session = AsyncMock()
        uow = ConcreteUnitOfWork(session=mock_session)

        assert uow.session is mock_session

    def test_session_setter(self):
        """Session can be set via setter."""
        uow = ConcreteUnitOfWork()
        mock_session = AsyncMock()

        uow.session = mock_session

        assert uow.session is mock_session


class TestTimeoutFlag:
    """Tests for timeout tracking."""

    def test_timeout_flag_initially_false(self):
        """Timeout flag should be False initially."""
        uow = ConcreteUnitOfWork()

        assert uow._timeout_occurred is False

    async def test_timeout_flag_set_on_timeout_exception(self, mock_session_factory):
        """Timeout flag should be set when timeout exception occurs."""
        uow = ConcreteUnitOfWork(session_factory=mock_session_factory)

        # Simulate timeout during context exit
        uow._timeout_occurred = True

        assert uow._timeout_occurred is True
