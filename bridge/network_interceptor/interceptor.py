"""
Network request/response interception using Playwright's route and response listeners.

Captures ChatGPT API responses and streaming data via fetch interception and event listeners.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Optional

from playwright.async_api import Page, Response

from bridge.browser_controller.session import BrowserSession
from bridge.logging_setup import get_logger


class InterceptedMessage:
    """Represents a captured API message."""

    def __init__(
        self,
        request_id: str,
        conversation_id: Optional[str] = None,
        message_id: Optional[str] = None,
        role: str = "assistant",
        content: str = "",
        timestamp: Optional[datetime] = None,
        is_complete: bool = False,
    ):
        self.request_id = request_id
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.utcnow()
        self.is_complete = is_complete

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "is_complete": self.is_complete,
        }


class NetworkInterceptor:
    """Intercepts and captures network traffic from ChatGPT API calls."""

    def __init__(self, session: BrowserSession):
        """
        Initialize network interceptor.

        Args:
            session: BrowserSession instance to capture messages into
        """
        self.session = session
        self._logger = get_logger("network_interceptor")
        self._active = False
        self._conversation_id: Optional[str] = None
        self._current_message_id: Optional[str] = None
        self._response_buffer: str = ""
        self._request_counter: int = 0

    async def attach(self) -> None:
        """
        Attach Playwright event listeners to session.page.

        Sets up:
        1. Response listener for API calls
        2. JavaScript fetch interception
        3. Event listener for stream chunks
        """
        self._logger.info("attaching_network_interceptor", session_id=self.session.session_id)

        try:
            # 1. Listen for API responses
            self.session.page.on("response", self._on_response)

            # 2. Inject JavaScript to intercept fetch and dispatch events
            await self.session.page.add_init_script(self._get_fetch_interceptor_js())

            # 3. Expose Python function to be called from JavaScript
            await self.session.page.expose_function("bridge_on_chunk", self._on_stream_chunk)

            # 4. Set up event listener in page
            await self.session.page.evaluate(
                """
                window.addEventListener('bridge:stream_chunk', (e) => {
                    if (typeof window.bridge_on_chunk === 'function') {
                        window.bridge_on_chunk(e.detail);
                    }
                });
                """
            )

            self._active = True
            self._logger.info("network_interceptor_attached", session_id=self.session.session_id)
        except Exception as e:
            self._logger.error(
                "network_interceptor_attach_failed",
                session_id=self.session.session_id,
                error=str(e),
            )
            raise

    async def detach(self) -> None:
        """Detach network interceptor."""
        self._active = False
        self._logger.info("network_interceptor_detached", session_id=self.session.session_id)

    async def _on_response(self, response: Response) -> None:
        """
        Handle response events from Playwright.

        Extracts conversation_id and message data from backend-api responses.

        Args:
            response: Playwright Response object
        """
        try:
            url = response.url
            if "backend-api/conversation" not in url:
                return

            self._logger.debug("response_intercepted", url=url)

            # Extract conversation_id from URL
            url_match = re.search(r"/c/([a-zA-Z0-9-]+)", url)
            if url_match:
                self._conversation_id = url_match.group(1)
                self.session.conversation_id = self._conversation_id

            # Try to read response body as JSON
            try:
                body = await response.json()
                self._process_response_body(body, url)
            except Exception as e:
                self._logger.debug("response_json_parse_failed", url=url, error=str(e))

        except Exception as e:
            self._logger.warning("response_handler_error", error=str(e))

    def _process_response_body(self, body: dict, url: str) -> None:
        """
        Process response body to extract message data.

        Args:
            body: Parsed JSON response body
            url: Response URL
        """
        try:
            # Handle ChatGPT response format
            if "message" in body:
                message = body["message"]
                message_id = message.get("id")
                role = message.get("author", {}).get("role", "assistant")

                # Extract content from content.parts
                content_text = ""
                if "content" in message:
                    parts = message["content"].get("parts", [])
                    if parts:
                        content_text = parts[0] if isinstance(parts[0], str) else ""

                if message_id and content_text:
                    self._current_message_id = message_id
                    captured_msg = {
                        "request_id": f"api_{self._request_counter}",
                        "conversation_id": self._conversation_id,
                        "message_id": message_id,
                        "role": role,
                        "content": content_text,
                        "timestamp": datetime.utcnow().isoformat(),
                        "source": "api_response",
                    }
                    self._logger.debug("message_captured_from_response", message_id=message_id)
                    asyncio.create_task(self.session.put_captured_message(captured_msg))
        except Exception as e:
            self._logger.debug("response_body_processing_error", error=str(e))

    async def _on_stream_chunk(self, detail: dict) -> None:
        """
        Handle stream chunk from JavaScript.

        Processes Server-Sent Events (SSE) formatted chunks.

        Args:
            detail: Dictionary with 'chunk' and 'url' keys
        """
        try:
            chunk = detail.get("chunk", "")
            url = detail.get("url", "")

            if not chunk or "backend-api" not in url:
                return

            # Parse SSE format: lines like "data: {...}\n\n"
            lines = chunk.split("\n")

            for line in lines:
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue

                data_str = line[6:]  # Remove "data: " prefix

                # Check for stream end marker
                if data_str == "[DONE]":
                    self._logger.debug("stream_complete")
                    await self._finalize_response()
                    continue

                # Parse JSON chunk
                try:
                    data = json.loads(data_str)
                    await self._process_stream_data(data)
                except json.JSONDecodeError:
                    self._logger.debug("stream_chunk_json_parse_failed", chunk=data_str[:100])

        except Exception as e:
            self._logger.warning("stream_chunk_handler_error", error=str(e))

    async def _process_stream_data(self, data: dict) -> None:
        """
        Process individual stream data chunk.

        Extracts tokens from OpenAI/ChatGPT streaming format.

        Args:
            data: Parsed JSON chunk from stream
        """
        try:
            # Try OpenAI format: data.choices[0].delta.content
            if "choices" in data:
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    token = delta.get("content")
                    if token:
                        await self.session.put_stream_token(token)
                        self._response_buffer += token
                        self._logger.debug("stream_token_captured", token_len=len(token))
                        return

            # Try ChatGPT native format: data.message.content.parts[-1]
            if "message" in data:
                message = data.get("message", {})
                content = message.get("content", {})
                parts = content.get("parts", [])
                if parts:
                    last_part = parts[-1]
                    if isinstance(last_part, str):
                        # Accumulate new content
                        if len(last_part) > len(self._response_buffer):
                            token = last_part[len(self._response_buffer) :]
                            if token:
                                await self.session.put_stream_token(token)
                                self._response_buffer = last_part

        except Exception as e:
            self._logger.debug("stream_data_processing_error", error=str(e))

    async def _finalize_response(self) -> None:
        """Finalize streaming response and send complete message."""
        try:
            if self._response_buffer:
                final_msg = {
                    "request_id": f"stream_{self._request_counter}",
                    "conversation_id": self._conversation_id,
                    "message_id": self._current_message_id,
                    "role": "assistant",
                    "content": self._response_buffer,
                    "timestamp": datetime.utcnow().isoformat(),
                    "is_complete": True,
                    "source": "stream",
                }
                await self.session.put_captured_message(final_msg)
                self._logger.info("response_finalized", content_len=len(self._response_buffer))
        except Exception as e:
            self._logger.warning("finalize_response_error", error=str(e))

    def get_accumulated_response(self) -> str:
        """
        Get the accumulated response buffer.

        Returns:
            Accumulated response string
        """
        return self._response_buffer

    def reset(self) -> None:
        """Reset accumulated response and message state."""
        self._response_buffer = ""
        self._current_message_id = None
        self._request_counter += 1
        self._logger.debug("interceptor_reset", request_counter=self._request_counter)

    def _get_fetch_interceptor_js(self) -> str:
        """
        Get JavaScript code for fetch interception.

        Returns:
            JavaScript code as string
        """
        return """
        (function() {
            if (window.__bridge_fetch_installed) return;
            window.__bridge_fetch_installed = true;

            const origFetch = window.fetch;
            window.fetch = async function(...args) {
                const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
                const response = await origFetch.apply(this, args);

                if (url.includes('/backend-api/conversation') || url.includes('/backend-api/chat')) {
                    const clone = response.clone();
                    try {
                        const reader = clone.body.getReader();
                        const decoder = new TextDecoder();

                        (async () => {
                            try {
                                let fullChunk = '';
                                while (true) {
                                    const { done, value } = await reader.read();
                                    if (done) break;
                                    const chunk = decoder.decode(value, { stream: true });
                                    fullChunk += chunk;

                                    // Dispatch chunk events
                                    const event = new CustomEvent('bridge:stream_chunk', {
                                        detail: { chunk: chunk, url: url }
                                    });
                                    window.dispatchEvent(event);
                                }
                            } catch(e) {}
                        })();
                    } catch(e) {}
                }
                return response;
            };

            // Also patch XMLHttpRequest for fallback
            const origOpen = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url, ...args) {
                this._bridge_url = url;
                return origOpen.apply(this, [method, url, ...args]);
            };

            const origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(...args) {
                if (this._bridge_url && this._bridge_url.includes('/backend-api')) {
                    const origOnReadyStateChange = this.onreadystatechange;
                    this.onreadystatechange = function() {
                        if (this.readyState === 4) {
                            try {
                                const data = JSON.parse(this.responseText);
                                const event = new CustomEvent('bridge:xhr_response', {
                                    detail: { data: data, url: this._bridge_url }
                                });
                                window.dispatchEvent(event);
                            } catch(e) {}
                        }
                        if (origOnReadyStateChange) {
                            origOnReadyStateChange.call(this);
                        }
                    };
                }
                return origSend.apply(this, args);
            };
        })();
        """
