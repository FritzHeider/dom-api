"""
WebSocket message capture using Playwright's websocket event.

Captures real-time streaming data from WebSocket connections.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from playwright.async_api import WebSocket

from bridge.browser_controller.session import BrowserSession
from bridge.logging_setup import get_logger


class WebSocketCapture:
    """Captures WebSocket messages from ChatGPT connections."""

    def __init__(self, session: BrowserSession):
        """
        Initialize WebSocket capture.

        Args:
            session: BrowserSession instance to capture messages into
        """
        self.session = session
        self._logger = get_logger("ws_capture")
        self._active = False
        self._ws_connections: dict[str, WebSocket] = {}
        self._message_buffer: dict[str, str] = {}

    async def attach(self) -> None:
        """
        Attach WebSocket event listener to session.page.

        Sets up:
        1. Playwright websocket event listener
        2. JavaScript WebSocket constructor wrapping
        3. Message handler exposure
        """
        self._logger.info("attaching_websocket_capture", session_id=self.session.session_id)

        try:
            # 1. Listen for WebSocket connections
            self.session.page.on("websocket", self._on_websocket)

            # 2. Inject JavaScript to intercept WebSocket
            await self.session.page.add_init_script(self._get_websocket_interceptor_js())

            # 3. Expose Python function for JS to call
            await self.session.page.expose_function("bridge_on_ws_message", self._on_ws_message)

            self._active = True
            self._logger.info("websocket_capture_attached", session_id=self.session.session_id)
        except Exception as e:
            self._logger.error(
                "websocket_capture_attach_failed",
                session_id=self.session.session_id,
                error=str(e),
            )
            raise

    async def _on_websocket(self, ws: WebSocket) -> None:
        """
        Handle new WebSocket connection.

        Args:
            ws: Playwright WebSocket object
        """
        try:
            url = ws.url
            self._ws_connections[url] = ws
            self._message_buffer[url] = ""

            self._logger.info("websocket_connected", url=url)

            # Set up frame listeners
            ws.on(
                "framesent",
                lambda payload: asyncio.ensure_future(
                    self._on_ws_frame(url, payload, "sent")
                ),
            )
            ws.on(
                "framereceived",
                lambda payload: asyncio.ensure_future(
                    self._on_ws_frame(url, payload, "received")
                ),
            )
            ws.on("close", lambda: self._on_ws_close(url))
        except Exception as e:
            self._logger.warning("websocket_connection_handler_error", error=str(e))

    async def _on_ws_frame(self, url: str, payload, direction: str) -> None:
        """
        Handle WebSocket frame event.

        Args:
            url: WebSocket URL
            payload: Frame payload
            direction: "sent" or "received"
        """
        try:
            if direction != "received":
                return

            # Try to get text data
            body_text: Optional[str] = None
            if hasattr(payload, "text"):
                try:
                    body_text = payload.text()
                except Exception:
                    pass
            elif hasattr(payload, "binary"):
                try:
                    body_text = payload.binary().decode("utf-8")
                except Exception:
                    pass

            if not body_text:
                return

            # Try to parse as JSON
            try:
                data = json.loads(body_text)
                await self._process_ws_data(url, data)
            except json.JSONDecodeError:
                self._logger.debug("websocket_frame_json_parse_failed", url=url)

        except Exception as e:
            self._logger.debug("websocket_frame_handler_error", error=str(e))

    async def _process_ws_data(self, url: str, data: dict) -> None:
        """
        Process WebSocket data.

        Looks for token/chunk/content data in the message.

        Args:
            url: WebSocket URL
            data: Parsed JSON message
        """
        try:
            # Look for content token in various formats
            token = None

            # Format 1: Direct token field
            if "token" in data:
                token = data["token"]
            # Format 2: Nested in choices
            elif "choices" in data and data["choices"]:
                delta = data["choices"][0].get("delta", {})
                token = delta.get("content")
            # Format 3: Nested in delta
            elif "delta" in data:
                token = data["delta"].get("content")
            # Format 4: Nested in message
            elif "message" in data:
                parts = data["message"].get("content", {}).get("parts", [])
                if parts and isinstance(parts[-1], str):
                    token = parts[-1]

            if token:
                await self.session.put_stream_token(token)
                self._message_buffer[url] += token
                self._logger.debug(
                    "websocket_token_captured",
                    url=url,
                    token_len=len(token),
                )
        except Exception as e:
            self._logger.debug("websocket_data_processing_error", error=str(e))

    async def _on_ws_message(self, url: str, data: str) -> None:
        """
        Handle message from JavaScript hook.

        Called when JS wrapper detects a message.

        Args:
            url: WebSocket URL
            data: Message data as string
        """
        try:
            if not data:
                return

            # Try to parse as JSON
            try:
                parsed = json.loads(data)
                await self._process_ws_data(url, parsed)
            except json.JSONDecodeError:
                self._logger.debug("websocket_message_json_parse_failed", url=url)
        except Exception as e:
            self._logger.warning("websocket_message_handler_error", error=str(e))

    def _on_ws_close(self, url: str) -> None:
        """
        Handle WebSocket close event.

        Args:
            url: WebSocket URL
        """
        try:
            self._ws_connections.pop(url, None)
            accumulated = self._message_buffer.pop(url, "")
            self._logger.info(
                "websocket_closed",
                url=url,
                accumulated_len=len(accumulated),
            )
        except Exception as e:
            self._logger.debug("websocket_close_handler_error", error=str(e))

    async def detach(self) -> None:
        """Detach WebSocket capture."""
        self._active = False
        self._ws_connections.clear()
        self._message_buffer.clear()
        self._logger.info("websocket_capture_detached", session_id=self.session.session_id)

    def get_accumulated_message(self, url: Optional[str] = None) -> str:
        """
        Get accumulated message from WebSocket.

        Args:
            url: Specific WebSocket URL, or None for first connection

        Returns:
            Accumulated message string
        """
        if url:
            return self._message_buffer.get(url, "")
        # Return first connection's buffer
        if self._message_buffer:
            return next(iter(self._message_buffer.values()))
        return ""

    def reset(self) -> None:
        """Reset accumulated message buffers."""
        self._message_buffer.clear()
        self._logger.debug("websocket_capture_reset")

    def _get_websocket_interceptor_js(self) -> str:
        """
        Get JavaScript code for WebSocket interception.

        Returns:
            JavaScript code as string
        """
        return """
        (function() {
            if (window.__bridge_ws_installed) return;
            window.__bridge_ws_installed = true;

            const OriginalWebSocket = window.WebSocket;

            window.WebSocket = function(url, ...args) {
                const ws = new OriginalWebSocket(url, ...args);

                const origSend = ws.send;
                ws.send = function(data) {
                    if (typeof window.bridge_on_ws_message === 'function') {
                        try {
                            window.bridge_on_ws_message(url, data.toString());
                        } catch(e) {}
                    }
                    return origSend.call(this, data);
                };

                const origOnMessage = ws.onmessage;
                ws.onmessage = function(event) {
                    if (typeof window.bridge_on_ws_message === 'function') {
                        try {
                            window.bridge_on_ws_message(url, event.data);
                        } catch(e) {}
                    }
                    if (origOnMessage) {
                        return origOnMessage.call(this, event);
                    }
                };

                ws.addEventListener('message', function(event) {
                    if (typeof window.bridge_on_ws_message === 'function') {
                        try {
                            window.bridge_on_ws_message(url, event.data);
                        } catch(e) {}
                    }
                });

                return ws;
            };

            // Copy prototype
            window.WebSocket.prototype = OriginalWebSocket.prototype;
            window.WebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
            window.WebSocket.OPEN = OriginalWebSocket.OPEN;
            window.WebSocket.CLOSING = OriginalWebSocket.CLOSING;
            window.WebSocket.CLOSED = OriginalWebSocket.CLOSED;
        })();
        """
