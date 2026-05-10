import time
import tempfile
import os
from datetime import datetime
from typing import Optional, Callable

from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    BackgroundTasks,
    WebSocket,
    Request,
    Response,
)
from fastapi.responses import FileResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from bridge.config import settings
from bridge.api.models import (
    ChatRequest,
    ChatResponse,
    NewChatRequest,
    NewChatResponse,
    StatusResponse,
    SessionsResponse,
    RequestStatusResponse,
    MetricsResponse,
    ErrorResponse,
)
from bridge.api.streaming import stream_manager
from bridge.queue.models import QueueRequest, Priority, RequestStatus
from bridge.queue.manager import QueueManager
from bridge.session_pool.pool import SessionPool
from bridge.prompt_engine.engine import PromptEngine, PromptResult
from bridge.recovery.recovery import RecoveryManager
from bridge.logging_setup import get_logger

logger = get_logger("routes")
router = APIRouter()


# ── Dependency Injection ───────────────────────────────────────────────────────


def get_queue_manager(request: Request) -> QueueManager:
    """Retrieve QueueManager from app state."""
    return request.app.state.queue_manager


def get_session_pool(request: Request) -> SessionPool:
    """Retrieve SessionPool from app state."""
    return request.app.state.session_pool


def get_recovery_manager(request: Request) -> RecoveryManager:
    """Retrieve RecoveryManager from app state."""
    return request.app.state.recovery_manager


def get_start_time(request: Request) -> float:
    """Retrieve application start time."""
    return request.app.state.start_time


# ── POST /chat ─────────────────────────────────────────────────────────────────


@router.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def post_chat(
    body: ChatRequest,
    background_tasks: BackgroundTasks,
    queue: QueueManager = Depends(get_queue_manager),
    pool: SessionPool = Depends(get_session_pool),
    recovery: RecoveryManager = Depends(get_recovery_manager),
) -> ChatResponse:
    """
    Submit a prompt and get a response.

    If stream=True, returns a request_id immediately and streams tokens via SSE
    at GET /stream/{request_id}.

    If stream=False, blocks until response is ready (up to response_wait_timeout_s).
    """
    priority_map = {
        "low": Priority.LOW,
        "normal": Priority.NORMAL,
        "high": Priority.HIGH,
    }
    priority = priority_map.get(body.priority.lower(), Priority.NORMAL)

    request = QueueRequest(
        prompt=body.prompt,
        conversation_id=body.conversation_id,
        new_chat=body.new_chat,
        priority=priority,
        max_retries=settings.queue_max_retry,
    )

    if body.stream:
        # Non-blocking: enqueue and return immediately
        await queue.enqueue(request)
        background_tasks.add_task(
            _execute_prompt_background,
            request,
            pool,
            queue,
            recovery,
        )
        logger.info(
            "Chat request queued for streaming",
            request_id=request.request_id,
            priority=body.priority,
        )
        return ChatResponse(
            request_id=request.request_id,
            response="",
            conversation_id=body.conversation_id,
            latency_ms=0.0,
            tokens_streamed=True,
            source="streaming",
            timestamp=datetime.utcnow().isoformat(),
        )

    # Blocking: execute synchronously
    try:
        result = await _execute_prompt_sync(request, pool, queue, recovery)
        logger.info(
            "Chat request completed",
            request_id=result.request_id,
            latency_ms=result.latency_ms,
        )
        return ChatResponse(
            request_id=result.request_id,
            response=result.response,
            conversation_id=result.conversation_id,
            latency_ms=result.latency_ms,
            tokens_streamed=result.tokens_streamed,
            source=result.source,
            timestamp=datetime.utcnow().isoformat(),
        )
    except Exception as e:
        logger.error(
            "Chat request failed",
            request_id=request.request_id,
            error=str(e),
        )
        raise


# ── POST /new_chat ────────────────────────────────────────────────────────────


@router.post("/new_chat", response_model=NewChatResponse, tags=["Chat"])
async def post_new_chat(
    body: NewChatRequest,
    queue: QueueManager = Depends(get_queue_manager),
    pool: SessionPool = Depends(get_session_pool),
    recovery: RecoveryManager = Depends(get_recovery_manager),
) -> NewChatResponse:
    """
    Start a new chat conversation, optionally with an initial prompt.
    Returns a new conversation_id.
    """
    initial_prompt = body.prompt or "Hello"

    request = QueueRequest(
        prompt=initial_prompt,
        new_chat=True,
        priority=Priority.HIGH,
        max_retries=settings.queue_max_retry,
    )

    try:
        result = await _execute_prompt_sync(request, pool, queue, recovery)
        logger.info(
            "New chat created",
            request_id=result.request_id,
            conversation_id=result.conversation_id,
        )
        return NewChatResponse(
            request_id=result.request_id,
            conversation_id=result.conversation_id,
            response=result.response if body.prompt else None,
            message="New chat created successfully",
        )
    except Exception as e:
        logger.error(
            "New chat creation failed",
            request_id=request.request_id,
            error=str(e),
        )
        raise


# ── GET /response/{request_id} ─────────────────────────────────────────────────


@router.get("/response/{request_id}", response_model=RequestStatusResponse, tags=["Chat"])
async def get_response(
    request_id: str,
    queue: QueueManager = Depends(get_queue_manager),
) -> RequestStatusResponse:
    """
    Poll for the result of a previously submitted chat request.
    Returns RequestStatus with result once available.
    """
    req = await queue.get_result(request_id)
    if not req:
        raise HTTPException(
            status_code=404,
            detail=f"Request {request_id} not found",
        )

    return RequestStatusResponse(
        request_id=req.request_id,
        status=req.status.value,
        result=req.result,
        conversation_id=req.conversation_id,
        latency_ms=req.latency_ms,
        error=req.error,
        timestamp=req.completed_at.isoformat() if req.completed_at else None,
    )


# ── GET /status ────────────────────────────────────────────────────────────────


@router.get("/status", response_model=StatusResponse, tags=["System"])
async def get_status(
    queue: QueueManager = Depends(get_queue_manager),
    pool: SessionPool = Depends(get_session_pool),
    start_time: float = Depends(get_start_time),
) -> StatusResponse:
    """System health and status overview."""
    try:
        queue_stats = await queue.get_stats()
        redis_ok = True
    except Exception as e:
        logger.warning("Queue stats retrieval failed", error=str(e))
        queue_stats = None
        redis_ok = False

    pool_status = pool.get_status()

    # Determine overall health
    error_sessions = pool_status.get("error", 0)
    total_sessions = pool_status.get("total", 1)

    if not redis_ok or total_sessions == 0:
        health = "unhealthy"
    elif error_sessions > total_sessions // 2:
        health = "degraded"
    else:
        health = "healthy"

    return StatusResponse(
        status=health,
        uptime_s=time.monotonic() - start_time,
        sessions=pool_status,
        queue=queue_stats.model_dump() if queue_stats else {},
        redis_connected=redis_ok,
        timestamp=datetime.utcnow().isoformat(),
    )


# ── GET /sessions ──────────────────────────────────────────────────────────────


@router.get("/sessions", response_model=SessionsResponse, tags=["System"])
async def get_sessions(
    pool: SessionPool = Depends(get_session_pool),
) -> SessionsResponse:
    """List all active browser sessions and their states."""
    pool_status = pool.get_status()
    return SessionsResponse(
        sessions=pool_status.get("sessions", []),
        pool_status=pool_status,
    )


# ── GET /sessions/{session_id}/screenshot ──────────────────────────────────────


@router.get(
    "/sessions/{session_id}/screenshot",
    tags=["Debug"],
)
async def get_session_screenshot(
    session_id: str,
    pool: SessionPool = Depends(get_session_pool),
) -> FileResponse:
    """
    Capture a screenshot of the specified session's browser.
    Returns PNG image file.
    """
    # Access internal sessions dict
    sessions = getattr(pool, "_sessions", {})
    session = sessions.get(session_id)

    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id} not found",
        )

    try:
        from bridge.ui_fallback.scraper import DOMScraper

        scraper = DOMScraper(session)
        with tempfile.NamedTemporaryFile(
            suffix=".png",
            delete=False,
        ) as f:
            temp_path = f.name

        await scraper.take_screenshot(temp_path)

        return FileResponse(
            temp_path,
            media_type="image/png",
            filename=f"{session_id}.png",
        )

    except Exception as e:
        logger.error(
            "Screenshot capture failed",
            session_id=session_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to capture screenshot: {str(e)}",
        )


# ── GET /metrics ───────────────────────────────────────────────────────────────


@router.get("/metrics", response_model=MetricsResponse, tags=["System"])
async def get_metrics(
    request: Request,
    queue: QueueManager = Depends(get_queue_manager),
    pool: SessionPool = Depends(get_session_pool),
    start_time: float = Depends(get_start_time),
) -> MetricsResponse:
    """Human-readable metrics summary."""
    metrics_store = request.app.state.metrics_store
    queue_stats = await queue.get_stats()
    pool_status = pool.get_status()

    latencies = metrics_store.get("latencies", [])
    if latencies:
        sorted_latencies = sorted(latencies)
        p95_idx = int(len(sorted_latencies) * 0.95)
        p95 = sorted_latencies[p95_idx] if p95_idx < len(sorted_latencies) else 0.0
        avg = sum(latencies) / len(latencies)
    else:
        p95 = 0.0
        avg = 0.0

    total = metrics_store.get("total_requests", 0)
    success = metrics_store.get("successful_requests", 0)

    return MetricsResponse(
        total_requests=total,
        successful_requests=success,
        failed_requests=total - success,
        success_rate=success / total if total > 0 else 0.0,
        average_latency_ms=avg,
        p95_latency_ms=p95,
        active_sessions=pool_status.get("busy", 0),
        queue_length=queue_stats.total_pending if queue_stats else 0,
        recovery_events=request.app.state.recovery_manager.get_recovery_stats().get(
            "total_recoveries", 0
        ),
        uptime_s=time.monotonic() - start_time,
    )


# ── GET /stream/{request_id} (SSE) ─────────────────────────────────────────────


@router.get("/stream/{request_id}", tags=["Streaming"])
async def stream_sse(request_id: str) -> StreamingResponse:
    """
    Server-Sent Events stream for a given request_id.
    Streams tokens until completion or error.
    """

    async def generator():
        async for event in stream_manager.sse_generator(
            request_id,
            timeout_s=settings.response_wait_timeout_s,
        ):
            yield event

    return EventSourceResponse(generator())


# ── WebSocket /ws/stream/{request_id} ──────────────────────────────────────────


@router.websocket("/ws/stream/{request_id}")
async def stream_ws(websocket: WebSocket, request_id: str) -> None:
    """
    WebSocket stream for a given request_id.
    Streams tokens and completion event, then closes.
    """
    await stream_manager.handle_websocket(
        websocket,
        request_id,
        timeout_s=settings.response_wait_timeout_s,
    )


# ── DELETE /request/{request_id} ───────────────────────────────────────────────


@router.delete("/request/{request_id}", tags=["Chat"])
async def cancel_request(
    request_id: str,
    queue: QueueManager = Depends(get_queue_manager),
) -> dict:
    """Cancel a pending request."""
    try:
        await queue.cancel_request(request_id)
        logger.info("Request cancelled", request_id=request_id)
        return {"message": f"Request {request_id} cancelled"}
    except Exception as e:
        logger.error(
            "Failed to cancel request",
            request_id=request_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel request: {str(e)}",
        )


# ── Internal Helpers ───────────────────────────────────────────────────────────


async def _execute_prompt_sync(
    request: QueueRequest,
    pool: SessionPool,
    queue: QueueManager,
    recovery: RecoveryManager,
) -> PromptResult:
    """
    Acquire session, run prompt engine, release session.
    Implements retry logic with recovery.
    """
    last_error: Optional[Exception] = None

    for attempt in range(settings.recovery_max_retries + 1):
        session = None
        try:
            # Acquire session from pool
            session = await pool.acquire(timeout_s=30.0)
            if not session:
                raise RuntimeError("No sessions available")

            # Initialize engine
            engine = PromptEngine(session)
            await engine.initialize()

            # Token callback for streaming
            async def token_callback(token: str) -> None:
                await stream_manager.publish_token(request.request_id, token)

            # Execute prompt
            result = await engine.execute(request, token_callback=token_callback)

            # Success - release and return
            await pool.release(session)

            # Record in queue
            await queue.complete_request(
                request.request_id,
                result.response,
                result.latency_ms,
                result.tokens_streamed,
            )

            # Notify streaming subscribers
            await stream_manager.publish_complete(
                request.request_id,
                result.response,
                result.conversation_id,
                result.latency_ms,
            )

            logger.info(
                "Prompt executed successfully",
                request_id=request.request_id,
                attempt=attempt + 1,
                latency_ms=result.latency_ms,
            )

            return result

        except Exception as e:
            last_error = e
            logger.warning(
                "Prompt execution failed",
                request_id=request.request_id,
                attempt=attempt + 1,
                error=str(e),
            )

            if session:
                await pool.release_with_error(session, str(e))

            # Try recovery if not last attempt
            if attempt < settings.recovery_max_retries:
                try:
                    recovered = await recovery.handle_execution_error(
                        session,
                        request,
                        e,
                    )
                    if not recovered:
                        continue
                except Exception as recovery_error:
                    logger.error(
                        "Recovery failed",
                        request_id=request.request_id,
                        error=str(recovery_error),
                    )

    # All retries exhausted
    if last_error:
        error_msg = str(last_error)
        await queue.fail_request(request.request_id, error_msg)
        await stream_manager.publish_error(request.request_id, error_msg)
        raise HTTPException(
            status_code=500,
            detail=f"Prompt execution failed after {settings.recovery_max_retries} retries: {error_msg}",
        )

    raise HTTPException(
        status_code=500,
        detail="Prompt execution failed",
    )


async def _execute_prompt_background(
    request: QueueRequest,
    pool: SessionPool,
    queue: QueueManager,
    recovery: RecoveryManager,
) -> None:
    """
    Background task version for streaming requests.
    Executes prompt and publishes updates via stream_manager.
    """
    try:
        await _execute_prompt_sync(request, pool, queue, recovery)
    except HTTPException:
        # Already handled in _execute_prompt_sync
        pass
    except Exception as e:
        logger.error(
            "Background prompt execution error",
            request_id=request.request_id,
            error=str(e),
        )
