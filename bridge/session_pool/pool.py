"""
Dynamic session pool manager.
Creates and manages 1-10 browser sessions on demand.
Scales up under load, scales down during idle periods.
Includes health checks and automatic recovery.
"""

import asyncio
from datetime import datetime
from typing import Optional

from bridge.browser_controller.controller import BrowserController
from bridge.browser_controller.session import BrowserSession, SessionStatus
from bridge.config import settings
from bridge.logging_setup import get_logger


class SessionPool:
    """
    Manages a dynamic pool of browser sessions.
    - Minimum: settings.session_pool_min (default 1)
    - Maximum: settings.session_pool_max (default 10)
    - Scales up on demand when all sessions are busy
    - Scales down by closing idle sessions after idle timeout
    - Recovers from session errors automatically
    """

    def __init__(self, browser_controller: BrowserController):
        self.controller = browser_controller
        self._sessions: dict[str, BrowserSession] = {}
        self._available: asyncio.Queue[str] = asyncio.Queue()  # session_ids of idle sessions
        self._lock = asyncio.Lock()
        self._logger = get_logger("session_pool")
        self._running = False
        self._maintenance_task: Optional[asyncio.Task] = None
        self._session_counter = 0

    async def start(self):
        """Start pool with minimum number of sessions."""
        self._running = True
        for i in range(settings.session_pool_min):
            await self._create_session()
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        self._logger.info("Session pool started", min_sessions=settings.session_pool_min)

    async def stop(self):
        """Graceful shutdown — close all sessions and stop maintenance."""
        self._running = False
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
        for session in list(self._sessions.values()):
            try:
                await self.controller.close_session(session.session_id)
            except Exception as e:
                self._logger.warning(
                    "Error closing session", session_id=session.session_id, error=str(e)
                )
        self._sessions.clear()
        self._logger.info("Session pool stopped")

    async def _create_session(self) -> BrowserSession:
        """Create a new browser session and add to pool."""
        self._session_counter += 1
        session_id = f"session_{self._session_counter:03d}"
        session = await self.controller.create_session(session_id)
        self._sessions[session_id] = session
        await self._available.put(session_id)
        self._logger.info("Session created", session_id=session_id)
        return session

    async def acquire(self, timeout_s: float = 30.0) -> Optional[BrowserSession]:
        """
        Acquire an available session from the pool.
        Creates a new one if under max and none are available.
        Waits up to timeout_s for a session to become available.
        Returns None if timeout reached.
        """
        try:
            # Try to get immediately available session
            session_id = self._available.get_nowait()
            session = self._sessions.get(session_id)
            if session and session.status not in (SessionStatus.CLOSED, SessionStatus.ERROR):
                session.status = SessionStatus.BUSY
                self._logger.debug("Session acquired from pool", session_id=session_id)
                return session
        except asyncio.QueueEmpty:
            pass

        # No available session — try to create one if under max
        async with self._lock:
            active = len(
                [s for s in self._sessions.values() if s.status != SessionStatus.CLOSED]
            )
            if active < settings.session_pool_max:
                session = await self._create_session()
                # Remove from available since we're acquiring it
                try:
                    self._available.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                session.status = SessionStatus.BUSY
                self._logger.debug("New session created and acquired", session_id=session.session_id)
                return session

        # Wait for one to become available
        try:
            session_id = await asyncio.wait_for(
                self._available.get(), timeout=timeout_s
            )
            session = self._sessions.get(session_id)
            if session:
                session.status = SessionStatus.BUSY
                self._logger.debug("Session acquired after waiting", session_id=session_id)
                return session
        except asyncio.TimeoutError:
            self._logger.error("Timeout waiting for available session")
            return None

        return None

    async def release(self, session: BrowserSession):
        """Return session to pool after successful use."""
        session.status = SessionStatus.IDLE
        session.last_activity = datetime.utcnow()
        await self._available.put(session.session_id)
        self._logger.debug("Session released to pool", session_id=session.session_id)

    async def release_with_error(self, session: BrowserSession, error: str):
        """
        Release session that encountered an error.
        Marks it for recovery instead of returning to available pool.
        """
        session.status = SessionStatus.ERROR
        session.error_count += 1
        self._logger.warning(
            "Session released with error",
            session_id=session.session_id,
            error_count=session.error_count,
            error=error,
        )
        # Schedule recovery instead of returning to available pool
        asyncio.create_task(self._recover_session(session.session_id))

    async def _recover_session(self, session_id: str):
        """
        Restart a failed session.
        Waits recovery_session_restart_delay_s before attempting restart.
        """
        await asyncio.sleep(settings.recovery_session_restart_delay_s)
        try:
            session = await self.controller.restart_session(session_id)
            self._sessions[session_id] = session
            await self._available.put(session_id)
            self._logger.info("Session recovered", session_id=session_id)
        except Exception as e:
            self._logger.error(
                "Session recovery failed",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )

    async def _maintenance_loop(self):
        """
        Periodic maintenance task running every 60 seconds:
        - Clean up idle sessions beyond minimum pool size
        - Ensure we maintain minimum session count
        """
        while self._running:
            await asyncio.sleep(60)
            try:
                await self._cleanup_idle_sessions()
                await self._ensure_min_sessions()
            except Exception as e:
                self._logger.error("Maintenance error", error=str(e), exc_info=True)

    async def _cleanup_idle_sessions(self):
        """
        Remove excess idle sessions beyond minimum pool size.
        Only removes if idle longer than session_idle_timeout_s.
        """
        now = datetime.utcnow()
        idle_sessions = [
            s
            for s in self._sessions.values()
            if s.status == SessionStatus.IDLE
            and (now - s.last_activity).total_seconds() > settings.session_idle_timeout_s
        ]
        active_count = len(
            [s for s in self._sessions.values() if s.status != SessionStatus.CLOSED]
        )
        to_close = max(0, active_count - settings.session_pool_min)

        for session in idle_sessions[:to_close]:
            try:
                await self.controller.close_session(session.session_id)
                self._sessions.pop(session.session_id, None)
                self._logger.info("Idle session closed", session_id=session.session_id)
            except Exception as e:
                self._logger.warning(
                    "Error closing idle session",
                    session_id=session.session_id,
                    error=str(e),
                )

    async def _ensure_min_sessions(self):
        """Ensure we have at least session_pool_min healthy sessions."""
        active = len(
            [
                s
                for s in self._sessions.values()
                if s.status not in (SessionStatus.CLOSED, SessionStatus.ERROR)
            ]
        )
        if active < settings.session_pool_min:
            needed = settings.session_pool_min - active
            self._logger.info("Restoring minimum sessions", needed=needed, current=active)
            for _ in range(needed):
                await self._create_session()

    def get_status(self) -> dict:
        """Get current pool status for monitoring."""
        sessions_list = [s.to_dict() for s in self._sessions.values()]
        return {
            "total": len(self._sessions),
            "idle": len([s for s in self._sessions.values() if s.status == SessionStatus.IDLE]),
            "busy": len([s for s in self._sessions.values() if s.status == SessionStatus.BUSY]),
            "error": len(
                [s for s in self._sessions.values() if s.status == SessionStatus.ERROR]
            ),
            "closed": len(
                [s for s in self._sessions.values() if s.status == SessionStatus.CLOSED]
            ),
            "sessions": sessions_list,
        }
