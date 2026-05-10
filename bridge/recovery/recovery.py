"""
Self-healing recovery manager.
Detects failure conditions and applies healing strategies.
Uses exponential backoff with jitter for retries.
"""

import asyncio
import random
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from bridge.browser_controller.session import BrowserSession, SessionStatus
from bridge.config import settings
from bridge.logging_setup import get_logger
from bridge.queue.manager import QueueManager
from bridge.queue.models import QueueRequest
from bridge.session_pool.pool import SessionPool


class RecoveryReason(str, Enum):
    """Reasons for recovery activation."""

    SESSION_EXPIRED = "session_expired"
    PAGE_CRASHED = "page_crashed"
    GENERATION_STUCK = "generation_stuck"
    NETWORK_TIMEOUT = "network_timeout"
    LOGIN_REQUIRED = "login_required"
    SELECTOR_FAILURE = "selector_failure"
    UNKNOWN_ERROR = "unknown_error"


class RecoveryAction(str, Enum):
    """Available recovery actions."""

    RETRY_PROMPT = "retry_prompt"
    RELOAD_PAGE = "reload_page"
    RESTART_SESSION = "restart_session"
    CREATE_NEW_CHAT = "create_new_chat"
    WAIT_AND_RETRY = "wait_and_retry"


class RecoveryManager:
    """
    Detects failure conditions and applies healing strategies.
    Classifies errors and determines appropriate recovery action.
    Tracks recovery history for monitoring and debugging.
    Uses exponential backoff with jitter to avoid thundering herd.
    """

    def __init__(self, session_pool: SessionPool, queue_manager: QueueManager):
        self.pool = session_pool
        self.queue = queue_manager
        self._logger = get_logger("recovery")
        self._recovery_history: list[dict] = []

    def classify_error(self, error: Exception) -> Tuple[RecoveryReason, RecoveryAction]:
        """
        Map exception to recovery strategy.
        Returns: (reason, action) tuple
        """
        err_str = str(error).lower()

        if "timeout" in err_str or "time out" in err_str:
            return RecoveryReason.NETWORK_TIMEOUT, RecoveryAction.RETRY_PROMPT
        if "login" in err_str or "session" in err_str or "auth" in err_str:
            return RecoveryReason.SESSION_EXPIRED, RecoveryAction.RELOAD_PAGE
        if "selector" in err_str or "locator" in err_str or "cannot locate" in err_str:
            return RecoveryReason.SELECTOR_FAILURE, RecoveryAction.RELOAD_PAGE
        if "crash" in err_str or "closed" in err_str:
            return RecoveryReason.PAGE_CRASHED, RecoveryAction.RESTART_SESSION
        if "generating" in err_str or "stuck" in err_str:
            return RecoveryReason.GENERATION_STUCK, RecoveryAction.CREATE_NEW_CHAT

        return RecoveryReason.UNKNOWN_ERROR, RecoveryAction.RETRY_PROMPT

    async def handle_execution_error(
        self, session: BrowserSession, request: QueueRequest, error: Exception
    ) -> bool:
        """
        Handle an execution error and attempt recovery.
        Returns True if recovery succeeded or retry should be attempted.
        Returns False if recovery failed and request should be abandoned.
        """
        reason, action = self.classify_error(error)

        self._logger.warning(
            "Recovery triggered",
            session_id=session.session_id,
            request_id=request.request_id,
            reason=reason.value,
            action=action.value,
            error=str(error),
        )

        # Track recovery attempt
        self._recovery_history.append(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "session_id": session.session_id,
                "request_id": request.request_id,
                "reason": reason.value,
                "action": action.value,
                "error": str(error),
            }
        )

        # Calculate backoff with jitter
        backoff = settings.recovery_backoff_base_s * (2 ** request.retry_count)
        backoff = min(backoff + random.uniform(0, 1), 30.0)  # cap at 30s, add jitter

        if action == RecoveryAction.RETRY_PROMPT:
            await asyncio.sleep(backoff)
            return True  # Caller should retry with same request

        elif action == RecoveryAction.RELOAD_PAGE:
            try:
                await session.page.reload(wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(2.0)
                await session.selector_discovery.rediscover_all()
                self._logger.info("Page reloaded successfully", session_id=session.session_id)
                return True
            except Exception as e:
                self._logger.error("Page reload failed", session_id=session.session_id, error=str(e))
                return False

        elif action == RecoveryAction.RESTART_SESSION:
            try:
                new_session = await self.pool.controller.restart_session(session.session_id)
                self.pool._sessions[session.session_id] = new_session
                self._logger.info("Session restarted successfully", session_id=session.session_id)
                return True
            except Exception as e:
                self._logger.error("Session restart failed", session_id=session.session_id, error=str(e))
                return False

        elif action == RecoveryAction.CREATE_NEW_CHAT:
            try:
                await session.new_conversation()
                self._logger.info("New conversation created", session_id=session.session_id)
                return True
            except Exception as e:
                self._logger.error(
                    "New conversation failed", session_id=session.session_id, error=str(e)
                )
                return False

        elif action == RecoveryAction.WAIT_AND_RETRY:
            await asyncio.sleep(backoff)
            return True

        return False

    async def check_session_health(self, session: BrowserSession) -> bool:
        """
        Check if session is healthy and responsive.
        Returns True if healthy, False otherwise.
        """
        try:
            # Check page is responsive
            await asyncio.wait_for(session.page.evaluate("1 + 1"), timeout=5.0)

            # Check we're still on ChatGPT
            url = session.page.url
            if "chatgpt.com" not in url and "chat.openai.com" not in url:
                self._logger.warning("Session URL incorrect", session_id=session.session_id, url=url)
                return False

            return True
        except Exception as e:
            self._logger.warning(
                "Session health check failed", session_id=session.session_id, error=str(e)
            )
            return False

    async def check_all_sessions(self):
        """Health check all sessions in pool and recover unhealthy ones."""
        for session in list(self.pool._sessions.values()):
            if session.status not in (SessionStatus.CLOSED, SessionStatus.RESTARTING):
                try:
                    healthy = await self.check_session_health(session)
                    if not healthy:
                        self._logger.warning(
                            "Unhealthy session detected", session_id=session.session_id
                        )
                        asyncio.create_task(self.pool._recover_session(session.session_id))
                except Exception as e:
                    self._logger.error(
                        "Health check error",
                        session_id=session.session_id,
                        error=str(e),
                        exc_info=True,
                    )

    def get_recovery_stats(self) -> dict:
        """Get recovery statistics and history."""
        total = len(self._recovery_history)
        if not total:
            return {"total_recoveries": 0}

        reason_counts = {}
        for r in self._recovery_history:
            reason = r["reason"]
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        return {
            "total_recoveries": total,
            "by_reason": reason_counts,
            "recent": self._recovery_history[-10:],
        }

    def clear_history(self):
        """Clear recovery history (useful for testing/cleanup)."""
        self._recovery_history.clear()
