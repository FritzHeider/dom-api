"""
Individual browser session management for ChatGPT DOM Agent Bridge.
Handles session lifecycle, selector discovery, and state management.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from enum import Enum
from typing import Optional

from playwright.async_api import Page, BrowserContext, Browser

try:
    from playwright_stealth import stealth_async as _stealth_async
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

from bridge.config import BridgeConfig
from bridge.logging_setup import get_logger
from bridge.selector_system.selector_discovery import SelectorDiscovery, SelectorStore


class SessionStatus(str, Enum):
    """Session lifecycle states."""

    IDLE = "idle"
    BUSY = "busy"
    GENERATING = "generating"
    ERROR = "error"
    RESTARTING = "restarting"
    CLOSED = "closed"


class BrowserSession:
    """Manages a single persistent browser session for ChatGPT interaction."""

    def __init__(
        self,
        session_id: str,
        page: Page,
        context: BrowserContext,
        browser: Browser,
        settings: BridgeConfig,
    ):
        """
        Initialize a new browser session.

        Args:
            session_id: Unique session identifier
            page: Playwright Page instance
            context: Playwright BrowserContext instance
            browser: Playwright Browser reference
            settings: BridgeConfig with session settings
        """
        self.session_id = session_id
        self.page = page
        self.context = context
        self.browser = browser
        self.settings = settings
        self.status = SessionStatus.IDLE
        self.conversation_id: Optional[str] = None
        self.last_activity: datetime = datetime.utcnow()
        self.request_count: int = 0
        self.error_count: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        self.selector_discovery: Optional[SelectorDiscovery] = None
        self._captured_messages: asyncio.Queue = asyncio.Queue()
        self._stream_tokens: asyncio.Queue = asyncio.Queue()
        self.logger = get_logger("browser_session")

    # URL fragments that indicate a broken/error page that should be redirected away
    _ERROR_URL_PATTERNS = ("/api/auth/error", "/auth/error", "error=")

    async def _check_page_ok(self) -> None:
        """
        If the current URL is an auth/error page, navigate back to the base
        ChatGPT URL and log a clear warning instead of crashing downstream.
        """
        url = self.page.url
        if any(pat in url for pat in self._ERROR_URL_PATTERNS):
            self.logger.warning(
                "auth_error_page_detected",
                url=url,
                action="redirecting_to_base_url",
            )
            try:
                await self.page.goto(self.settings.chatgpt_url, wait_until="domcontentloaded")
                self.logger.info("redirected_to_base_url")
            except Exception as e:
                self.logger.error("redirect_failed", error=str(e))

    async def initialize(self) -> None:
        """
        Initialize the session: navigate to ChatGPT, wait for readiness, discover selectors.
        """
        async with self._lock:
            self.logger.info("session_initialize_started", session_id=self.session_id)

            # Apply stealth patches before navigation
            if _STEALTH_AVAILABLE:
                await _stealth_async(self.page)
                self.logger.debug("stealth_applied", session_id=self.session_id)
            else:
                self.logger.warning("playwright_stealth_not_available")

            # Navigate to ChatGPT URL
            try:
                await self.page.goto(self.settings.chatgpt_url, wait_until="domcontentloaded")
                self.logger.debug("page_navigated", url=self.settings.chatgpt_url)
            except Exception as e:
                self.logger.error("page_navigation_failed", error=str(e))
                self.status = SessionStatus.ERROR
                raise

            # Bail out of auth/error pages immediately
            await self._check_page_ok()

            # Wait for network idle (optional, may timeout)
            try:
                await asyncio.wait_for(
                    self.page.wait_for_load_state("networkidle"),
                    timeout=self.settings.browser_timeout_ms / 1000,
                )
                self.logger.debug("network_idle_achieved")
            except asyncio.TimeoutError:
                self.logger.warning("network_idle_timeout")

            # Initialize selector discovery
            session_store_path = self.settings.session_profile_path(self.session_id) / "selectors.json"
            selector_store = SelectorStore(cache_path=session_store_path)
            self.selector_discovery = SelectorDiscovery(self.page, store=selector_store)

            # Run initial discovery
            try:
                results = await self.selector_discovery.rediscover_all()
                successful = sum(1 for v in results.values() if v is not None)
                self.logger.info("selectors_discovered", successful=successful, total=len(results))
            except Exception as e:
                self.logger.error("selector_discovery_failed", error=str(e))
                self.status = SessionStatus.ERROR
                raise

            self.status = SessionStatus.IDLE
            self.last_activity = datetime.utcnow()
            self.logger.info("session_initialize_completed", session_id=self.session_id)

    async def is_logged_in(self) -> bool:
        """
        Check if the user is logged in to ChatGPT.

        Returns:
            True if logged in, False otherwise
        """
        try:
            # Check for login URL indicators
            current_url = self.page.url
            if "/auth/" in current_url or "login" in current_url:
                return False

            # Check for prompt textarea presence
            input_selector = "#prompt-textarea"
            element_count = await self.page.locator(input_selector).count()
            if element_count > 0:
                return True

            # Check for profile button
            profile_check = await self.page.evaluate(
                "() => document.querySelector('[data-testid=\"profile-button\"]') != null"
            )
            return bool(profile_check)
        except Exception as e:
            self.logger.warning("login_check_failed", error=str(e))
            return False

    async def wait_for_login(self, timeout_s: int = 120) -> bool:
        """
        Wait for user to log in. Polls every 3 seconds.

        Args:
            timeout_s: Maximum seconds to wait

        Returns:
            True if logged in, False if timeout
        """
        self.logger.info("waiting_for_login", timeout_s=timeout_s)
        start_time = datetime.utcnow()

        while True:
            if await self.is_logged_in():
                self.logger.info("login_confirmed")
                return True

            elapsed = (datetime.utcnow() - start_time).total_seconds()
            if elapsed > timeout_s:
                self.logger.error("login_timeout")
                return False

            await asyncio.sleep(3)

    async def new_conversation(self) -> None:
        """Start a new conversation by navigating to base ChatGPT URL."""
        async with self._lock:
            self.logger.info("new_conversation_started", session_id=self.session_id)

            try:
                await self.page.goto(self.settings.chatgpt_url, wait_until="domcontentloaded")
                await self._check_page_ok()
                await asyncio.sleep(1)  # Brief wait for UI to settle

                # Wait for input to be ready
                try:
                    await asyncio.wait_for(
                        self.page.wait_for_selector("#prompt-textarea", timeout=self.settings.browser_timeout_ms),
                        timeout=self.settings.browser_timeout_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    self.logger.warning("input_not_ready_after_navigation")

                self.conversation_id = None
                self.status = SessionStatus.IDLE

                # Rediscover selectors
                if self.selector_discovery:
                    await self.selector_discovery.rediscover_all()

                self.logger.info("new_conversation_ready")
            except Exception as e:
                self.logger.error("new_conversation_failed", error=str(e))
                self.status = SessionStatus.ERROR
                raise

    async def extract_conversation_id(self) -> Optional[str]:
        """
        Extract conversation ID from current page.

        Returns:
            Conversation ID or None
        """
        try:
            # Try extracting from URL
            url_match = re.search(r"/c/([a-zA-Z0-9-]+)", self.page.url)
            if url_match:
                conv_id = url_match.group(1)
                self.logger.debug("conversation_id_extracted_from_url", id=conv_id)
                return conv_id

            # Try extracting from __NEXT_DATA__
            next_data = await self.page.evaluate(
                """
                () => {
                    try {
                        return window.__NEXT_DATA__?.props?.pageProps?.serverResponse?.data?.id;
                    } catch (e) {
                        return null;
                    }
                }
                """
            )
            if next_data:
                self.logger.debug("conversation_id_extracted_from_next_data", id=next_data)
                return next_data

            return None
        except Exception as e:
            self.logger.warning("conversation_id_extraction_failed", error=str(e))
            return None

    async def put_captured_message(self, message: dict) -> None:
        """Add a captured message to the queue."""
        await self._captured_messages.put(message)

    async def get_captured_message(self, timeout_s: float = 30.0) -> Optional[dict]:
        """
        Get a captured message from the queue.

        Args:
            timeout_s: Timeout in seconds

        Returns:
            Message dict or None if timeout
        """
        try:
            return await asyncio.wait_for(self._captured_messages.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None

    async def put_stream_token(self, token: str) -> None:
        """Add a streamed token to the queue."""
        await self._stream_tokens.put(token)

    async def get_stream_token(self, timeout_s: float = 5.0) -> Optional[str]:
        """
        Get a streamed token from the queue.

        Args:
            timeout_s: Timeout in seconds

        Returns:
            Token string or None if timeout
        """
        try:
            return await asyncio.wait_for(self._stream_tokens.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        """Close the session page. The persistent context is closed by BrowserController.stop()."""
        async with self._lock:
            self.status = SessionStatus.CLOSED
            self.logger.info("session_closing", session_id=self.session_id)

            try:
                await self.page.close()
            except Exception as e:
                self.logger.warning("page_close_failed", error=str(e))

            self.logger.info("session_closed", session_id=self.session_id)

    def to_dict(self) -> dict:
        """
        Convert session state to JSON-serializable dict.

        Returns:
            Dictionary with session metadata
        """
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "conversation_id": self.conversation_id,
            "last_activity": self.last_activity.isoformat(),
            "request_count": self.request_count,
            "error_count": self.error_count,
        }
