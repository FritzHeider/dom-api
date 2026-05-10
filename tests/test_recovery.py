"""
Tests for the recovery manager system.
Tests error classification, recovery actions, session health checks, and failure tracking.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from typing import Optional
from enum import Enum


class ErrorType(str, Enum):
    TIMEOUT = "timeout"
    SELECTOR_FAILURE = "selector_failure"
    CRASH = "crash"
    LOGIN_REQUIRED = "login_required"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


class RecoveryAction(str, Enum):
    RETRY = "retry"
    RELOAD = "reload"
    RESTART_SESSION = "restart_session"
    RECREATE_CHAT = "recreate_chat"
    MANUAL_INTERVENTION = "manual_intervention"


class ExecutionError:
    """Represents an execution error."""

    def __init__(self, error_type: ErrorType, message: str):
        self.type = error_type
        self.message = message


class SessionPool:
    """Mock session pool."""

    async def release(self, session):
        pass

    async def create_new(self):
        return MagicMock()


class RecoveryStats:
    """Tracks recovery statistics."""

    def __init__(self):
        self.timeout_errors = 0
        self.selector_failures = 0
        self.crashes = 0
        self.login_required = 0
        self.recovery_attempts = 0
        self.successful_recoveries = 0
        self.failed_recoveries = 0


class RecoveryManager:
    """Manages error recovery and session health."""

    def __init__(self, session_pool: SessionPool):
        self.session_pool = session_pool
        self.stats = RecoveryStats()
        self.error_history = {}

    def classify_error(self, error: ExecutionError) -> RecoveryAction:
        """Classify error and determine recovery action."""
        if error.type == ErrorType.TIMEOUT:
            return RecoveryAction.RETRY

        elif error.type == ErrorType.SELECTOR_FAILURE:
            return RecoveryAction.RELOAD

        elif error.type == ErrorType.CRASH:
            return RecoveryAction.RESTART_SESSION

        elif error.type == ErrorType.LOGIN_REQUIRED:
            return RecoveryAction.RELOAD

        else:
            return RecoveryAction.RETRY

    async def handle_error(
        self,
        error: ExecutionError,
        session: Optional[MagicMock],
    ) -> dict:
        """Handle execution error with appropriate recovery action."""
        action = self.classify_error(error)
        self.stats.recovery_attempts += 1

        try:
            if action == RecoveryAction.RETRY:
                await asyncio.sleep(0.1)  # Brief delay before retry
                self.stats.successful_recoveries += 1
                return {"action": "retry", "delay_ms": 100}

            elif action == RecoveryAction.RELOAD:
                if session and hasattr(session, "reload"):
                    await session.reload()
                self.stats.successful_recoveries += 1
                return {"action": "reload"}

            elif action == RecoveryAction.RESTART_SESSION:
                if session:
                    await self.session_pool.release(session)
                new_session = await self.session_pool.create_new()
                self.stats.successful_recoveries += 1
                return {"action": "restart_session", "new_session": new_session}

            elif action == RecoveryAction.RECREATE_CHAT:
                self.stats.successful_recoveries += 1
                return {"action": "recreate_chat"}

            else:
                self.stats.failed_recoveries += 1
                return {"action": "manual_intervention"}

        except Exception as e:
            self.stats.failed_recoveries += 1
            return {"action": "failed", "error": str(e)}

    async def record_error(self, session_id: str, error: ExecutionError) -> None:
        """Record error in history."""
        if session_id not in self.error_history:
            self.error_history[session_id] = []

        self.error_history[session_id].append(error)

        # Update stats
        if error.type == ErrorType.TIMEOUT:
            self.stats.timeout_errors += 1
        elif error.type == ErrorType.SELECTOR_FAILURE:
            self.stats.selector_failures += 1
        elif error.type == ErrorType.CRASH:
            self.stats.crashes += 1
        elif error.type == ErrorType.LOGIN_REQUIRED:
            self.stats.login_required += 1

    async def check_session_health(self, session) -> bool:
        """Check if session is in healthy state."""
        try:
            # Check if page exists
            if not hasattr(session, "page") or session.page is None:
                return False

            # Check if we're on the right URL
            if hasattr(session.page, "url"):
                current_url = await session.page.url()
                if "login" in current_url.lower() or "signin" in current_url.lower():
                    return False

            # Try to evaluate simple script
            if hasattr(session.page, "evaluate"):
                result = await session.page.evaluate("1+1")
                if result != 2:
                    return False

            return True

        except Exception:
            return False

    async def check_wrong_url(self, session) -> bool:
        """Check if session is at wrong URL."""
        try:
            if hasattr(session.page, "url"):
                url = await session.page.url()
                if "login" in url.lower() or "signin" in url.lower():
                    return True
        except Exception:
            pass
        return False

    def get_stats(self) -> dict:
        """Get recovery statistics."""
        return {
            "timeouts": self.stats.timeout_errors,
            "selector_failures": self.stats.selector_failures,
            "crashes": self.stats.crashes,
            "login_required": self.stats.login_required,
            "recovery_attempts": self.stats.recovery_attempts,
            "successful_recoveries": self.stats.successful_recoveries,
            "failed_recoveries": self.stats.failed_recoveries,
        }


# Fixtures

@pytest.fixture
def session_pool():
    """Create mock session pool."""
    return SessionPool()


@pytest.fixture
def recovery_manager(session_pool):
    """Create recovery manager."""
    return RecoveryManager(session_pool)


@pytest.fixture
def mock_session():
    """Create mock session."""
    session = MagicMock()
    session.page = AsyncMock()
    return session


# Test Cases

@pytest.mark.asyncio
async def test_classify_timeout_error_returns_retry(recovery_manager):
    """Test that timeout errors classify as retry."""
    error = ExecutionError(ErrorType.TIMEOUT, "Request timed out")
    action = recovery_manager.classify_error(error)
    assert action == RecoveryAction.RETRY


@pytest.mark.asyncio
async def test_classify_selector_failure_returns_reload(recovery_manager):
    """Test that selector failures classify as reload."""
    error = ExecutionError(
        ErrorType.SELECTOR_FAILURE,
        "Cannot find chat input",
    )
    action = recovery_manager.classify_error(error)
    assert action == RecoveryAction.RELOAD


@pytest.mark.asyncio
async def test_classify_crash_returns_restart_session(recovery_manager):
    """Test that crashes classify as restart."""
    error = ExecutionError(ErrorType.CRASH, "Browser crashed")
    action = recovery_manager.classify_error(error)
    assert action == RecoveryAction.RESTART_SESSION


@pytest.mark.asyncio
async def test_classify_login_required_returns_reload(recovery_manager):
    """Test that login required errors classify as reload."""
    error = ExecutionError(ErrorType.LOGIN_REQUIRED, "Session expired")
    action = recovery_manager.classify_error(error)
    assert action == RecoveryAction.RELOAD


@pytest.mark.asyncio
async def test_handle_execution_error_records_history(recovery_manager, mock_session):
    """Test that errors are recorded in history."""
    error = ExecutionError(ErrorType.TIMEOUT, "Timeout")

    await recovery_manager.record_error("session_1", error)

    assert "session_1" in recovery_manager.error_history
    assert len(recovery_manager.error_history["session_1"]) == 1
    assert recovery_manager.error_history["session_1"][0].type == ErrorType.TIMEOUT


@pytest.mark.asyncio
async def test_handle_timeout_error(recovery_manager):
    """Test handling timeout error."""
    error = ExecutionError(ErrorType.TIMEOUT, "Timeout")
    result = await recovery_manager.handle_error(error, None)

    assert result["action"] == "retry"
    assert recovery_manager.stats.recovery_attempts == 1
    assert recovery_manager.stats.successful_recoveries == 1


@pytest.mark.asyncio
async def test_handle_selector_failure(recovery_manager, mock_session):
    """Test handling selector failure."""
    mock_session.reload = AsyncMock()

    error = ExecutionError(ErrorType.SELECTOR_FAILURE, "Selector not found")
    result = await recovery_manager.handle_error(error, mock_session)

    assert result["action"] == "reload"
    mock_session.reload.assert_called_once()


@pytest.mark.asyncio
async def test_handle_crash_error(recovery_manager, mock_session):
    """Test handling crash error."""
    error = ExecutionError(ErrorType.CRASH, "Browser crash")
    result = await recovery_manager.handle_error(error, mock_session)

    assert result["action"] == "restart_session"
    assert "new_session" in result


@pytest.mark.asyncio
async def test_recovery_stats_returns_counts(recovery_manager):
    """Test recovery statistics."""
    errors = [
        ExecutionError(ErrorType.TIMEOUT, "Timeout 1"),
        ExecutionError(ErrorType.TIMEOUT, "Timeout 2"),
        ExecutionError(ErrorType.SELECTOR_FAILURE, "Selector fail"),
        ExecutionError(ErrorType.CRASH, "Crash"),
    ]

    for i, error in enumerate(errors):
        await recovery_manager.record_error(f"session_{i}", error)

    stats = recovery_manager.get_stats()

    assert stats["timeouts"] == 2
    assert stats["selector_failures"] == 1
    assert stats["crashes"] == 1


@pytest.mark.asyncio
async def test_check_session_health_returns_true_for_healthy(recovery_manager):
    """Test session health check returns true for healthy session."""
    session = MagicMock()
    session.page = AsyncMock()
    session.page.url = AsyncMock(return_value="https://chat.openai.com/chat")
    session.page.evaluate = AsyncMock(return_value=2)

    is_healthy = await recovery_manager.check_session_health(session)

    assert is_healthy is True


@pytest.mark.asyncio
async def test_check_session_health_returns_false_for_no_page(recovery_manager):
    """Test session health check returns false when page is None."""
    session = MagicMock()
    session.page = None

    is_healthy = await recovery_manager.check_session_health(session)

    assert is_healthy is False


@pytest.mark.asyncio
async def test_check_session_health_returns_false_for_wrong_url(recovery_manager):
    """Test session health check returns false for login page."""
    session = MagicMock()
    session.page = AsyncMock()
    session.page.url = AsyncMock(return_value="https://chat.openai.com/auth/login")

    is_healthy = await recovery_manager.check_session_health(session)

    assert is_healthy is False


@pytest.mark.asyncio
async def test_check_wrong_url_detection(recovery_manager):
    """Test detection of wrong URL (login page)."""
    session = MagicMock()
    session.page = AsyncMock()
    session.page.url = AsyncMock(return_value="https://chat.openai.com/signin")

    is_wrong = await recovery_manager.check_wrong_url(session)

    assert is_wrong is True


@pytest.mark.asyncio
async def test_check_wrong_url_returns_false_for_correct_url(recovery_manager):
    """Test wrong URL check returns false for correct URL."""
    session = MagicMock()
    session.page = AsyncMock()
    session.page.url = AsyncMock(return_value="https://chat.openai.com/chat")

    is_wrong = await recovery_manager.check_wrong_url(session)

    assert is_wrong is False


@pytest.mark.asyncio
async def test_error_tracking_by_type(recovery_manager):
    """Test comprehensive error tracking."""
    test_errors = [
        (ErrorType.TIMEOUT, 3),
        (ErrorType.SELECTOR_FAILURE, 2),
        (ErrorType.CRASH, 1),
        (ErrorType.LOGIN_REQUIRED, 1),
    ]

    for error_type, count in test_errors:
        for i in range(count):
            error = ExecutionError(error_type, f"Error {i}")
            await recovery_manager.record_error(f"session_{error_type}", error)

    stats = recovery_manager.get_stats()

    assert stats["timeouts"] == 3
    assert stats["selector_failures"] == 2
    assert stats["crashes"] == 1
    assert stats["login_required"] == 1


@pytest.mark.asyncio
async def test_recovery_attempt_tracking(recovery_manager):
    """Test tracking recovery attempts."""
    initial_stats = recovery_manager.get_stats()
    assert initial_stats["recovery_attempts"] == 0

    error = ExecutionError(ErrorType.TIMEOUT, "Timeout")
    await recovery_manager.handle_error(error, None)

    updated_stats = recovery_manager.get_stats()
    assert updated_stats["recovery_attempts"] == 1
    assert updated_stats["successful_recoveries"] == 1


@pytest.mark.asyncio
async def test_failed_recovery_tracking(recovery_manager):
    """Test tracking failed recovery attempts."""
    session = MagicMock()
    session.reload = AsyncMock(side_effect=Exception("Reload failed"))

    error = ExecutionError(ErrorType.SELECTOR_FAILURE, "Selector fail")
    await recovery_manager.handle_error(error, session)

    stats = recovery_manager.get_stats()
    assert stats["recovery_attempts"] == 1
    assert stats["failed_recoveries"] == 1
