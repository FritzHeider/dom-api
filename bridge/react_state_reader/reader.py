"""
React state reader that injects JavaScript and parses results.

Extracts conversation state, messages, and response data from ChatGPT's React application.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from bridge.browser_controller.session import BrowserSession
from bridge.logging_setup import get_logger
from bridge.react_state_reader.js_hooks import (
    EXTRACT_CONVERSATION_ID_JS,
    EXTRACT_LATEST_RESPONSE_JS,
    EXTRACT_MESSAGES_JS,
    INJECT_FETCH_INTERCEPTOR_JS,
    IS_GENERATING_JS,
    REACT_FIBER_WALKER_JS,
)


class ReactStateReader:
    """Extracts React state and messages from ChatGPT application."""

    def __init__(self, session: BrowserSession):
        """
        Initialize React state reader.

        Args:
            session: BrowserSession instance to read state from
        """
        self.session = session
        self._logger = get_logger("react_state_reader")

    async def extract_messages(self) -> Optional[list[dict]]:
        """
        Extract messages from page state.

        Tries multiple strategies to find message data from Next.js props,
        Redux/Zustand stores, or React fiber tree.

        Returns:
            List of message objects or None if extraction fails
        """
        try:
            result = await self.session.page.evaluate(EXTRACT_MESSAGES_JS)

            if not result:
                self._logger.debug("no_messages_found")
                return None

            source = result.get("source", "unknown")
            data = result.get("data")

            if not isinstance(data, list):
                self._logger.debug("messages_not_list", source=source, type=type(data).__name__)
                return None

            self._logger.info("messages_extracted", source=source, count=len(data))

            # Normalize message structure
            normalized = []
            for msg in data:
                if isinstance(msg, dict):
                    normalized.append({
                        "id": msg.get("id"),
                        "role": msg.get("author", {}).get("role") if isinstance(msg.get("author"), dict) else msg.get("role"),
                        "content": self._extract_content(msg),
                        "created_at": msg.get("create_time"),
                    })

            return normalized

        except Exception as e:
            self._logger.warning("message_extraction_failed", error=str(e))
            return None

    def _extract_content(self, message: dict) -> str:
        """
        Extract text content from a message object.

        Handles various message formats used by ChatGPT API.

        Args:
            message: Message object

        Returns:
            Extracted text content
        """
        try:
            # Format 1: content.parts
            if "content" in message:
                content_obj = message["content"]
                if isinstance(content_obj, dict):
                    parts = content_obj.get("parts", [])
                    if parts and isinstance(parts[0], str):
                        return parts[0]
                    if parts and isinstance(parts[0], dict) and "text" in parts[0]:
                        return parts[0]["text"]

            # Format 2: Direct text field
            if "text" in message:
                return message["text"]

            # Format 3: Direct content field
            if isinstance(message.get("content"), str):
                return message["content"]

            # Format 4: Message content
            if "message" in message:
                msg_obj = message["message"]
                if isinstance(msg_obj, dict) and "content" in msg_obj:
                    parts = msg_obj["content"].get("parts", [])
                    if parts:
                        return parts[0] if isinstance(parts[0], str) else str(parts[0])

            return ""
        except Exception as e:
            self._logger.debug("content_extraction_error", error=str(e))
            return ""

    async def extract_latest_response(self) -> Optional[dict]:
        """
        Extract the latest assistant message from the page.

        Uses DOM selectors and attributes to find the most recent response.

        Returns:
            Dictionary with keys: message_id, content, html, is_complete, source
            Or None if extraction fails
        """
        try:
            result = await self.session.page.evaluate(EXTRACT_LATEST_RESPONSE_JS)

            if not result or not result.get("content"):
                self._logger.debug("no_latest_response_found")
                return None

            self._logger.info(
                "latest_response_extracted",
                source=result.get("source"),
                content_len=len(result.get("content", "")),
                is_complete=result.get("is_complete"),
            )
            return result

        except Exception as e:
            self._logger.warning("latest_response_extraction_failed", error=str(e))
            return None

    async def extract_conversation_id(self) -> Optional[str]:
        """
        Extract conversation ID from current page.

        Tries URL pattern, Next.js data, and other sources.

        Returns:
            Conversation ID string or None
        """
        try:
            result = await self.session.page.evaluate(EXTRACT_CONVERSATION_ID_JS)

            if not result:
                self._logger.debug("no_conversation_id_found")
                return None

            conv_id = result.get("id")
            source = result.get("source", "unknown")

            if conv_id:
                self._logger.debug("conversation_id_extracted", source=source, id=conv_id)
                self.session.conversation_id = conv_id
                return conv_id

            return None

        except Exception as e:
            self._logger.warning("conversation_id_extraction_failed", error=str(e))
            return None

    async def is_generating(self) -> bool:
        """
        Check if ChatGPT is still generating a response.

        Looks for stop button, loading indicators, and disabled send button.

        Returns:
            True if generating, False otherwise
        """
        try:
            result = await self.session.page.evaluate(IS_GENERATING_JS)
            if result:
                self._logger.debug("generating_detected")
            return bool(result)
        except Exception as e:
            self._logger.warning("generating_check_failed", error=str(e))
            return False

    async def walk_fiber_tree(self) -> list[dict]:
        """
        Walk React fiber tree to find state and props.

        Traverses the React fiber tree looking for message/conversation state.

        Returns:
            List of found state objects with type, dataType, and data
        """
        try:
            results = await self.session.page.evaluate(REACT_FIBER_WALKER_JS)

            if not results:
                self._logger.debug("no_fiber_state_found")
                return []

            self._logger.info("fiber_tree_walked", found_items=len(results))
            return results

        except Exception as e:
            self._logger.warning("fiber_tree_walk_failed", error=str(e))
            return []

    async def wait_for_response_complete(self, timeout_s: float = 120.0) -> Optional[str]:
        """
        Wait for ChatGPT to finish generating a response.

        Polls is_generating() periodically, then extracts the latest response.

        Args:
            timeout_s: Maximum seconds to wait

        Returns:
            Response text content or None if timeout/error
        """
        self._logger.info("waiting_for_response_complete", timeout_s=timeout_s)
        start_time = time.monotonic()

        while time.monotonic() - start_time < timeout_s:
            try:
                generating = await self.is_generating()

                if not generating:
                    # Generation complete, extract response
                    result = await self.extract_latest_response()
                    if result and result.get("content"):
                        content = result["content"]
                        self._logger.info(
                            "response_complete",
                            content_len=len(content),
                            elapsed_s=time.monotonic() - start_time,
                        )
                        return content

                    self._logger.debug("no_content_in_latest_response")
                    await asyncio.sleep(0.5)
                    continue

                # Still generating, wait a bit
                await asyncio.sleep(0.5)

            except Exception as e:
                self._logger.warning("wait_loop_error", error=str(e))
                await asyncio.sleep(1)

        self._logger.warning("wait_for_response_timeout", timeout_s=timeout_s)
        return None

    async def inject_fetch_interceptor(self) -> None:
        """
        Inject fetch interceptor JavaScript into the page.

        Allows capturing streaming responses from the ChatGPT API.
        """
        try:
            await self.session.page.add_init_script(INJECT_FETCH_INTERCEPTOR_JS)
            self._logger.info("fetch_interceptor_injected")
        except Exception as e:
            self._logger.warning("fetch_interceptor_injection_failed", error=str(e))

    async def poll_messages(
        self,
        interval_s: float = 1.0,
        timeout_s: float = 30.0,
    ) -> Optional[list[dict]]:
        """
        Poll for messages until new ones appear or timeout.

        Useful for detecting when a new message has been received.

        Args:
            interval_s: Polling interval in seconds
            timeout_s: Maximum time to poll

        Returns:
            List of messages or None if timeout
        """
        self._logger.info("polling_messages", interval_s=interval_s, timeout_s=timeout_s)
        start_time = time.monotonic()
        last_messages: Optional[list[dict]] = None

        while time.monotonic() - start_time < timeout_s:
            try:
                messages = await self.extract_messages()

                if messages is not None:
                    if last_messages is None or len(messages) > len(last_messages):
                        self._logger.info(
                            "new_messages_detected",
                            count=len(messages),
                            elapsed_s=time.monotonic() - start_time,
                        )
                        return messages

                    last_messages = messages

                await asyncio.sleep(interval_s)

            except Exception as e:
                self._logger.warning("poll_loop_error", error=str(e))
                await asyncio.sleep(interval_s)

        self._logger.warning("poll_messages_timeout", timeout_s=timeout_s)
        return None

    async def get_page_state_summary(self) -> dict[str, Any]:
        """
        Get a summary of current page state.

        Useful for debugging and monitoring.

        Returns:
            Dictionary with conversation_id, generating, message_count, latest_response_preview
        """
        try:
            conv_id = await self.extract_conversation_id()
            generating = await self.is_generating()
            messages = await self.extract_messages()
            latest = await self.extract_latest_response()

            summary = {
                "conversation_id": conv_id,
                "is_generating": generating,
                "message_count": len(messages) if messages else 0,
                "latest_response_preview": (
                    latest["content"][:100] + "..." if latest and latest.get("content") else None
                ),
                "url": self.session.page.url,
            }

            self._logger.info("page_state_summary", summary=summary)
            return summary

        except Exception as e:
            self._logger.warning("get_page_state_summary_failed", error=str(e))
            return {}
