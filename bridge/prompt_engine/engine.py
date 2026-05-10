"""
Prompt execution engine — core orchestrator for ChatGPT interaction.
Handles input location, submission, and multi-strategy response capture.
Includes token streaming, React state reading, and DOM fallback.
"""

import asyncio
import time
from typing import Optional, Callable, Tuple

from bridge.browser_controller.session import BrowserSession
from bridge.config import settings
from bridge.logging_setup import get_logger
from bridge.queue.models import QueueRequest
from bridge.network_interceptor.interceptor import NetworkInterceptor
from bridge.react_state_reader.reader import ReactStateReader
from bridge.ui_fallback.scraper import DOMScraper


class PromptResult:
    """Result of a prompt execution."""

    def __init__(
        self,
        request_id: str,
        response: str,
        conversation_id: Optional[str],
        latency_ms: float,
        tokens_streamed: bool,
        source: str,
        error: Optional[str] = None,
    ):
        self.request_id = request_id
        self.response = response
        self.conversation_id = conversation_id
        self.latency_ms = latency_ms
        self.tokens_streamed = tokens_streamed
        self.source = source  # "network_stream", "react_state", "network_message", "dom_fallback"
        self.error = error

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "response": self.response,
            "conversation_id": self.conversation_id,
            "latency_ms": self.latency_ms,
            "tokens_streamed": self.tokens_streamed,
            "source": self.source,
            "error": self.error,
        }


class PromptEngine:
    """
    Orchestrates the full lifecycle of a prompt execution:
    1. Navigate to new chat if requested
    2. Locate and fill chat input field
    3. Submit prompt via Enter key or button click
    4. Monitor for response via multiple strategies (network stream, React state, DOM)
    5. Extract conversation ID and return result
    """

    def __init__(self, session: BrowserSession):
        self.session = session
        self._network_interceptor = NetworkInterceptor(session)
        self._state_reader = ReactStateReader(session)
        self._dom_scraper = DOMScraper(session)
        self._logger = get_logger("prompt_engine").bind(session_id=session.session_id)
        self._initialized = False

    async def initialize(self):
        """Set up all interceptors and readers on the session page."""
        try:
            await self._network_interceptor.attach()
            self._initialized = True
            self._logger.info("Prompt engine initialized")
        except Exception as e:
            self._logger.error("Failed to initialize prompt engine", error=str(e), exc_info=True)
            raise

    async def execute(
        self, request: QueueRequest, token_callback: Optional[Callable] = None
    ) -> PromptResult:
        """
        Execute a prompt request end-to-end.
        Args:
            request: QueueRequest containing prompt and configuration
            token_callback: Optional async callable(token: str) for streaming tokens
        Returns:
            PromptResult with response, source, and metadata
        Raises:
            RuntimeError: If execution fails after all strategies exhausted
        """
        start_time = time.monotonic()

        try:
            # Step 1: Navigate to new chat if requested
            if request.new_chat or not request.conversation_id:
                self._logger.debug("Starting new conversation")
                await self.session.new_conversation()
                await asyncio.sleep(1.0)

            # Step 2: Reset interceptor state
            self._network_interceptor.reset()

            # Step 3: Find and fill input
            self._logger.debug("Filling input field", prompt_length=len(request.prompt))
            await self._fill_input(request.prompt)

            # Step 4: Submit
            self._logger.debug("Submitting prompt")
            await self._submit()

            # Step 5: Wait and capture response
            response, source, tokens_streamed = await self._capture_response(
                request, token_callback
            )

            # Step 6: Get conversation ID
            conv_id = await self._state_reader.extract_conversation_id()
            if not conv_id:
                conv_id = await self.session.extract_conversation_id()
            if conv_id:
                self.session.conversation_id = conv_id

            latency_ms = (time.monotonic() - start_time) * 1000

            self._logger.info(
                "Prompt executed successfully",
                request_id=request.request_id,
                latency_ms=latency_ms,
                source=source,
                tokens_streamed=tokens_streamed,
            )

            return PromptResult(
                request_id=request.request_id,
                response=response or "",
                conversation_id=conv_id or request.conversation_id,
                latency_ms=latency_ms,
                tokens_streamed=tokens_streamed,
                source=source,
            )

        except Exception as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            self._logger.error(
                "Prompt execution failed",
                request_id=request.request_id,
                latency_ms=latency_ms,
                error=str(e),
                exc_info=True,
            )
            raise

    async def _fill_input(self, prompt: str):
        """
        Find the chat input field and type the prompt.
        Uses selector discovery with fallback and rediscovery.
        """
        selector = self.session.selector_discovery

        # Try to get working selector
        candidate = await selector.find_chat_input()
        if not candidate:
            self._logger.debug("No cached selector, rediscovering")
            await selector.rediscover_all()
            candidate = await selector.find_chat_input()

        if not candidate:
            raise RuntimeError("Cannot locate chat input field")

        try:
            locator = self.session.page.locator(candidate.selector)
            await locator.click(timeout=settings.prompt_submit_timeout_s * 1000)
            await locator.fill("")  # Clear existing content
            await locator.type(prompt, delay=20)  # Human-like typing speed
            self._logger.debug("Prompt typed successfully", length=len(prompt))
        except Exception as e:
            # Mark selector as failed and try alternatives
            self.session.selector_discovery._store.mark_failed("chat_input")
            raise RuntimeError(f"Failed to fill input: {e}")

    async def _submit(self):
        """
        Submit the prompt.
        Tries Enter key first, falls back to submit button click.
        """
        # Try pressing Enter first (most reliable)
        try:
            await self.session.page.keyboard.press("Enter")
            self._logger.debug("Submitted via Enter key")
            return
        except Exception as e:
            self._logger.debug("Enter key failed", error=str(e))

        # Fall back to click submit button
        selector = self.session.selector_discovery
        candidate = await selector.find_submit_button()
        if candidate:
            try:
                await self.session.page.locator(candidate.selector).click(
                    timeout=settings.prompt_submit_timeout_s * 1000
                )
                self._logger.debug("Submitted via button click")
                return
            except Exception as e:
                self.session.selector_discovery._store.mark_failed("submit_button")
                raise RuntimeError(f"Failed to submit via button: {e}")

        raise RuntimeError("Cannot locate submit button as fallback")

    async def _capture_response(
        self, request: QueueRequest, token_callback: Optional[Callable] = None
    ) -> Tuple[str, str, bool]:
        """
        Capture response using multiple strategies in priority order.
        Returns: (response_text, source_name, tokens_streamed_flag)

        Strategies:
        1. Collect streaming tokens from network interceptor
        2. Wait for React state to show complete response
        3. Extract from captured network message
        4. Scrape from DOM
        """
        timeout = settings.response_wait_timeout_s

        # Give page a moment to start processing
        await asyncio.sleep(0.5)

        # Strategy 1: Collect streaming tokens from network interceptor
        tokens_collected = []
        collected_via_stream = False

        try:
            stream_timeout = getattr(settings, "stream_idle_timeout_s", 30.0)
            last_token_time = time.monotonic()

            while (time.monotonic() - last_token_time) < stream_timeout:
                token = await self.session.get_stream_token(timeout_s=2.0)
                if token is None:
                    # Check if generation is done
                    generating = await self._state_reader.is_generating()
                    if not generating and tokens_collected:
                        break
                    if not generating and not tokens_collected:
                        break
                    continue

                if token == "[DONE]":
                    break

                tokens_collected.append(token)
                last_token_time = time.monotonic()
                collected_via_stream = True

                if token_callback:
                    await token_callback(token)

        except Exception as e:
            self._logger.warning("Stream capture error", error=str(e))

        if tokens_collected:
            response = "".join(tokens_collected)
            if response.strip():
                self._logger.info(
                    "Response captured via streaming", tokens=len(tokens_collected)
                )
                return response, "network_stream", True

        # Strategy 2: React state reader with polling
        try:
            response = await self._state_reader.wait_for_response_complete(
                timeout_s=min(timeout, 30)
            )
            if response and response.strip():
                self._logger.info("Response captured via React state")
                return response, "react_state", False
        except Exception as e:
            self._logger.warning("React state reader failed", error=str(e))

        # Strategy 3: Captured network message
        msg = await self.session.get_captured_message(timeout_s=5.0)
        if msg and msg.get("content"):
            content = msg["content"]
            if content.strip():
                self._logger.info("Response captured via network message")
                return content, "network_message", False

        # Strategy 4: DOM fallback
        try:
            response = await self._dom_scraper.wait_for_response(timeout_s=min(timeout, 30))
            if response and response.strip():
                self._logger.info("Response captured via DOM fallback")
                return response, "dom_fallback", False
        except Exception as e:
            self._logger.warning("DOM scraper failed", error=str(e))

        raise RuntimeError("All response capture strategies exhausted")
