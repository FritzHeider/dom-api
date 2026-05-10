import asyncio
import json
from typing import Dict, List, Optional
from fastapi import WebSocket, WebSocketDisconnect

from bridge.logging_setup import get_logger


logger = get_logger("streaming")


class StreamManager:
    """
    Manages active streaming connections.
    Supports both SSE and WebSocket clients.
    Publishes tokens and completion events to all subscribers.
    """

    def __init__(self):
        # request_id -> list of asyncio.Queue (one per connected SSE client)
        self._sse_subscribers: Dict[str, List[asyncio.Queue]] = {}
        # request_id -> list of WebSocket connections
        self._ws_connections: Dict[str, List[WebSocket]] = {}
        self._logger = get_logger("streaming")

    async def publish_token(self, request_id: str, token: str) -> None:
        """
        Publish a streaming token to all subscribers of this request.
        Sends to both SSE and WebSocket clients.
        """
        message = {
            "type": "token",
            "token": token,
            "request_id": request_id,
        }

        # Publish to SSE subscribers
        if request_id in self._sse_subscribers:
            for queue in self._sse_subscribers[request_id]:
                try:
                    await queue.put(message)
                except asyncio.QueueFull:
                    self._logger.warning(
                        "SSE queue full, dropping token",
                        request_id=request_id,
                    )

        # Publish to WebSocket subscribers
        if request_id in self._ws_connections:
            dead_sockets = []
            for ws in self._ws_connections[request_id]:
                try:
                    await ws.send_json(message)
                except Exception as e:
                    self._logger.warning(
                        "Failed to send token to WebSocket",
                        error=str(e),
                        request_id=request_id,
                    )
                    dead_sockets.append(ws)

            for ws in dead_sockets:
                try:
                    self._ws_connections[request_id].remove(ws)
                except ValueError:
                    pass

    async def publish_complete(
        self,
        request_id: str,
        response: str,
        conversation_id: Optional[str],
        latency_ms: float,
    ) -> None:
        """
        Signal completion to all subscribers.
        Sends final response and closes streaming connection.
        """
        payload = {
            "type": "complete",
            "request_id": request_id,
            "response": response,
            "conversation_id": conversation_id,
            "latency_ms": latency_ms,
        }

        # Notify SSE subscribers
        if request_id in self._sse_subscribers:
            for queue in self._sse_subscribers[request_id]:
                try:
                    await queue.put(payload)
                    await queue.put(None)  # Sentinel to close stream
                except asyncio.QueueFull:
                    pass

        # Notify WebSocket subscribers
        if request_id in self._ws_connections:
            dead_sockets = []
            for ws in self._ws_connections[request_id]:
                try:
                    await ws.send_json(payload)
                    await ws.close()
                except Exception as e:
                    self._logger.warning(
                        "Error closing WebSocket",
                        error=str(e),
                        request_id=request_id,
                    )
                    dead_sockets.append(ws)

            for ws in dead_sockets:
                try:
                    self._ws_connections[request_id].remove(ws)
                except ValueError:
                    pass

            if not self._ws_connections[request_id]:
                del self._ws_connections[request_id]

    async def publish_error(self, request_id: str, error: str) -> None:
        """
        Signal error to all subscribers.
        Closes streaming connection after error.
        """
        payload = {
            "type": "error",
            "request_id": request_id,
            "error": error,
        }

        # Notify SSE subscribers
        if request_id in self._sse_subscribers:
            for queue in self._sse_subscribers[request_id]:
                try:
                    await queue.put(payload)
                    await queue.put(None)
                except asyncio.QueueFull:
                    pass

        # Notify WebSocket subscribers
        if request_id in self._ws_connections:
            dead_sockets = []
            for ws in self._ws_connections[request_id]:
                try:
                    await ws.send_json(payload)
                    await ws.close()
                except Exception as e:
                    self._logger.warning(
                        "Error closing WebSocket after error",
                        error=str(e),
                        request_id=request_id,
                    )
                    dead_sockets.append(ws)

            for ws in dead_sockets:
                try:
                    self._ws_connections[request_id].remove(ws)
                except ValueError:
                    pass

            if not self._ws_connections[request_id]:
                del self._ws_connections[request_id]

    def subscribe_sse(self, request_id: str) -> asyncio.Queue:
        """
        Create a new queue for SSE streaming.
        Returns asyncio.Queue that will receive events.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        if request_id not in self._sse_subscribers:
            self._sse_subscribers[request_id] = []
        self._sse_subscribers[request_id].append(queue)
        self._logger.debug(
            "SSE subscriber connected",
            request_id=request_id,
            subscriber_count=len(self._sse_subscribers[request_id]),
        )
        return queue

    def unsubscribe_sse(self, request_id: str, queue: asyncio.Queue) -> None:
        """Remove an SSE queue subscriber."""
        if request_id in self._sse_subscribers:
            try:
                self._sse_subscribers[request_id].remove(queue)
            except ValueError:
                pass
            if not self._sse_subscribers[request_id]:
                del self._sse_subscribers[request_id]
                self._logger.debug(
                    "SSE subscribers cleaned up",
                    request_id=request_id,
                )

    def subscribe_ws(self, request_id: str, websocket: WebSocket) -> None:
        """Register a WebSocket connection."""
        if request_id not in self._ws_connections:
            self._ws_connections[request_id] = []
        self._ws_connections[request_id].append(websocket)
        self._logger.debug(
            "WebSocket subscriber connected",
            request_id=request_id,
            subscriber_count=len(self._ws_connections[request_id]),
        )

    async def sse_generator(self, request_id: str, timeout_s: float = 120.0):
        """
        Async generator for SSE EventSourceResponse.
        Yields server-sent events until complete, error, or timeout.
        """
        queue = self.subscribe_sse(request_id)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_s

        try:
            while loop.time() < deadline:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break

                try:
                    msg = await asyncio.wait_for(
                        queue.get(),
                        timeout=min(5.0, remaining),
                    )
                    if msg is None:
                        # Sentinel value - stream closed
                        break

                    yield {
                        "data": json.dumps(msg),
                        "event": msg.get("type", "message"),
                    }

                    if msg.get("type") in ("complete", "error"):
                        break

                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield {"data": ": keepalive", "event": "ping"}

        except asyncio.CancelledError:
            self._logger.debug(
                "SSE stream cancelled",
                request_id=request_id,
            )
        finally:
            self.unsubscribe_sse(request_id, queue)

    async def handle_websocket(
        self,
        websocket: WebSocket,
        request_id: str,
        timeout_s: float = 120.0,
    ) -> None:
        """
        Handle a WebSocket connection for streaming.
        Sends tokens and completion event, then closes.
        Supports ping/pong keepalive.
        """
        await websocket.accept()
        self.subscribe_ws(request_id, websocket)

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_s

        try:
            # Send initial acknowledgement
            await websocket.send_json({
                "type": "connected",
                "request_id": request_id,
            })

            # Keep connection alive until disconnected or done
            while loop.time() < deadline:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break

                try:
                    msg = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=min(30.0, remaining),
                    )
                    # Handle client messages (e.g., ping)
                    if msg == "ping":
                        await websocket.send_json({"type": "pong"})

                except asyncio.TimeoutError:
                    # Send keepalive ping
                    await websocket.send_json({"type": "ping"})

                except WebSocketDisconnect:
                    self._logger.debug(
                        "WebSocket disconnected",
                        request_id=request_id,
                    )
                    break

        except Exception as e:
            self._logger.error(
                "WebSocket error",
                error=str(e),
                request_id=request_id,
            )
        finally:
            if request_id in self._ws_connections:
                try:
                    self._ws_connections[request_id].remove(websocket)
                except ValueError:
                    pass
                if not self._ws_connections[request_id]:
                    del self._ws_connections[request_id]


# Global singleton instance
stream_manager = StreamManager()
