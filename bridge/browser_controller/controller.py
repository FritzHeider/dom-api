"""
Browser lifecycle and session management controller.
Manages Playwright instance, session pool, and browser initialization.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from bridge.config import BridgeConfig
from bridge.logging_setup import get_logger
from bridge.browser_controller.session import BrowserSession, SessionStatus


class BrowserController:
    """Manages browser lifecycle and session creation/destruction."""

    # Realistic Chrome user agent — macOS to match the actual host OS
    CHROME_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: BridgeConfig):
        """
        Initialize the browser controller.

        Args:
            settings: BridgeConfig instance with browser settings
        """
        self.settings = settings
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        # Persistent contexts keyed by session_id (one per profile dir)
        self._contexts: dict[str, BrowserContext] = {}
        self._sessions: dict[str, BrowserSession] = {}
        self.logger = get_logger("browser_controller")

    async def start(self) -> None:
        """
        Start Playwright and launch browser instance.
        """
        self.logger.info("browser_controller_starting")

        try:
            self._playwright = await async_playwright().start()
            self.logger.debug("playwright_started")
            # Browser is launched lazily per-session via launch_persistent_context
            self.logger.info("browser_controller_ready")

        except Exception as e:
            self.logger.error("browser_launch_failed", error=str(e))
            raise

    async def create_session(self, session_id: str) -> BrowserSession:
        """
        Create a new browser session with persistent profile.

        Args:
            session_id: Unique session identifier

        Returns:
            Initialized BrowserSession instance

        Raises:
            RuntimeError: If browser is not started
            Exception: If session creation fails
        """
        if not self._playwright:
            raise RuntimeError("Browser not started. Call start() first.")

        self.logger.info("session_creation_started", session_id=session_id)

        try:
            # Persistent profile directory — cookies/session survive restarts
            profile_path = self.settings.session_profile_path(session_id)

            # Remove stale SingletonLock left by a previously crashed instance
            singleton_lock = profile_path / "SingletonLock"
            if singleton_lock.exists():
                singleton_lock.unlink()
                self.logger.warning("stale_singleton_lock_removed", session_id=session_id)

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
            ]

            context_kwargs = {
                "headless": self.settings.browser_headless,
                "viewport": {
                    "width": self.settings.browser_viewport_width,
                    "height": self.settings.browser_viewport_height,
                },
                "user_agent": self.CHROME_USER_AGENT,
                "locale": "en-US",
                "timezone_id": "America/New_York",
                "ignore_https_errors": True,
                "args": launch_args,
            }

            if self.settings.browser_slow_mo_ms > 0:
                context_kwargs["slow_mo"] = self.settings.browser_slow_mo_ms

            context = await self._playwright.chromium.launch_persistent_context(
                str(profile_path), **context_kwargs
            )
            self._contexts[session_id] = context
            # Expose browser ref via context for compatibility
            self._browser = context.browser
            self.logger.debug("browser_context_created", session_id=session_id,
                              profile=str(profile_path))

            # Reuse existing page or create new one
            pages = context.pages
            page = pages[0] if pages else await context.new_page()
            self.logger.debug("page_created", session_id=session_id)

            # Set navigation timeout
            page.set_default_timeout(self.settings.browser_timeout_ms)

            # Create session instance
            session = BrowserSession(session_id, page, context, self._browser, self.settings)

            # Initialize session (navigate, discover selectors)
            await session.initialize()

            # Store session
            self._sessions[session_id] = session
            self.logger.info("session_created", session_id=session_id)

            return session

        except Exception as e:
            self.logger.error("session_creation_failed", session_id=session_id, error=str(e))
            raise

    async def get_session(self, session_id: str) -> Optional[BrowserSession]:
        """
        Get an active session by ID.

        Args:
            session_id: Session identifier

        Returns:
            BrowserSession or None if not found
        """
        return self._sessions.get(session_id)

    async def close_session(self, session_id: str) -> None:
        """
        Close and remove a session, including its persistent context.
        Must close the context to release the SingletonLock before restart.

        Args:
            session_id: Session identifier
        """
        session = self._sessions.pop(session_id, None)
        if session:
            self.logger.info("session_closing", session_id=session_id)
            await session.close()
            self.logger.info("session_closed", session_id=session_id)

        # Close the persistent context so the profile's SingletonLock is released
        ctx = self._contexts.pop(session_id, None)
        if ctx:
            try:
                await ctx.close()
                self.logger.info("context_closed", session_id=session_id)
            except Exception as e:
                self.logger.warning("context_close_error", session_id=session_id, error=str(e))

    async def restart_session(self, session_id: str) -> BrowserSession:
        """
        Restart a session (close and recreate).

        Args:
            session_id: Session identifier

        Returns:
            New BrowserSession instance
        """
        self.logger.info("session_restart_started", session_id=session_id)

        # Close existing session
        await self.close_session(session_id)

        # Wait for recovery delay
        await asyncio.sleep(self.settings.recovery_session_restart_delay_s)

        # Create new session
        new_session = await self.create_session(session_id)
        self.logger.info("session_restarted", session_id=session_id)

        return new_session

    async def stop(self) -> None:
        """
        Stop browser and close all sessions.
        """
        self.logger.info("browser_controller_stopping")

        # Close all sessions (pages only — context closed separately)
        session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            try:
                await self.close_session(session_id)
            except Exception as e:
                self.logger.warning("session_close_error", session_id=session_id, error=str(e))

        # Close persistent contexts (saves cookies to disk)
        for session_id, ctx in list(self._contexts.items()):
            try:
                await ctx.close()
                self.logger.info("context_closed", session_id=session_id)
            except Exception as e:
                self.logger.warning("context_close_error", session_id=session_id, error=str(e))
        self._contexts.clear()

        # Stop Playwright
        if self._playwright:
            try:
                await self._playwright.stop()
                self.logger.info("playwright_stopped")
            except Exception as e:
                self.logger.warning("playwright_stop_error", error=str(e))

        self.logger.info("browser_controller_stopped")

    @property
    def active_session_count(self) -> int:
        """
        Get count of active (non-closed) sessions.

        Returns:
            Number of active sessions
        """
        return len([s for s in self._sessions.values() if s.status != SessionStatus.CLOSED])

    async def get_sessions_status(self) -> dict[str, dict]:
        """
        Get status of all sessions.

        Returns:
            Dict mapping session_id to session status dict
        """
        return {session_id: session.to_dict() for session_id, session in self._sessions.items()}

    async def collect_idle_sessions(self, idle_timeout_s: int) -> list[str]:
        """
        Find sessions that have been idle for longer than threshold.

        Args:
            idle_timeout_s: Idle timeout threshold in seconds

        Returns:
            List of idle session IDs
        """
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        idle_sessions = []

        for session_id, session in self._sessions.items():
            if session.status == SessionStatus.CLOSED:
                continue

            idle_duration = (now - session.last_activity).total_seconds()
            if idle_duration > idle_timeout_s:
                idle_sessions.append(session_id)

        return idle_sessions

    async def cleanup_idle_sessions(self, idle_timeout_s: int) -> int:
        """
        Close and remove idle sessions.

        Args:
            idle_timeout_s: Idle timeout threshold in seconds

        Returns:
            Number of sessions cleaned up
        """
        idle_sessions = await self.collect_idle_sessions(idle_timeout_s)

        for session_id in idle_sessions:
            self.logger.info("cleanup_idle_session", session_id=session_id)
            await self.close_session(session_id)

        return len(idle_sessions)
