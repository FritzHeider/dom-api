from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ChatRequest(BaseModel):
    """Client request for a chat prompt."""
    prompt: str = Field(..., min_length=1, max_length=100_000)
    conversation_id: Optional[str] = None
    new_chat: bool = False
    priority: str = "normal"  # "low", "normal", "high"
    stream: bool = False  # Whether client wants SSE streaming

    class Config:
        json_schema_extra = {
            "example": {
                "prompt": "What is 2+2?",
                "conversation_id": "conv_123",
                "new_chat": False,
                "priority": "normal",
                "stream": False,
            }
        }


class ChatResponse(BaseModel):
    """Response containing chat result."""
    request_id: str
    response: str
    conversation_id: Optional[str] = None
    latency_ms: float
    tokens_streamed: bool
    source: str  # "network_stream", "react_state", "dom_fallback", etc.
    timestamp: str  # ISO format

    class Config:
        json_schema_extra = {
            "example": {
                "request_id": "req_abc123",
                "response": "2+2 equals 4",
                "conversation_id": "conv_123",
                "latency_ms": 1234.5,
                "tokens_streamed": False,
                "source": "network_stream",
                "timestamp": "2026-03-14T10:30:00.000Z",
            }
        }


class NewChatRequest(BaseModel):
    """Request to create a new chat conversation."""
    prompt: Optional[str] = None  # Optional initial prompt for new chat

    class Config:
        json_schema_extra = {
            "example": {
                "prompt": "Tell me a story",
            }
        }


class NewChatResponse(BaseModel):
    """Response after creating new chat."""
    request_id: str
    conversation_id: Optional[str] = None
    response: Optional[str] = None
    message: str = "New chat created"

    class Config:
        json_schema_extra = {
            "example": {
                "request_id": "req_xyz789",
                "conversation_id": "conv_789",
                "response": "Once upon a time...",
                "message": "New chat created successfully",
            }
        }


class StatusResponse(BaseModel):
    """System health and status overview."""
    status: str  # "healthy", "degraded", "unhealthy"
    version: str = "1.0.0"
    uptime_s: float
    sessions: dict
    queue: dict
    redis_connected: bool
    timestamp: str

    class Config:
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "version": "1.0.0",
                "uptime_s": 3600.5,
                "sessions": {"total": 4, "busy": 2, "idle": 2, "error": 0},
                "queue": {"total_pending": 5, "total_completed": 100},
                "redis_connected": True,
                "timestamp": "2026-03-14T10:30:00.000Z",
            }
        }


class SessionsResponse(BaseModel):
    """List of all active sessions."""
    sessions: list
    pool_status: dict

    class Config:
        json_schema_extra = {
            "example": {
                "sessions": [
                    {
                        "session_id": "sess_001",
                        "status": "idle",
                        "conversation_id": None,
                        "request_count": 5,
                        "error_count": 0,
                    }
                ],
                "pool_status": {
                    "total": 4,
                    "busy": 1,
                    "idle": 3,
                    "error": 0,
                },
            }
        }


class RequestStatusResponse(BaseModel):
    """Status of a specific request."""
    request_id: str
    status: str
    result: Optional[str] = None
    conversation_id: Optional[str] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    timestamp: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "request_id": "req_abc123",
                "status": "completed",
                "result": "The answer is 4",
                "conversation_id": "conv_123",
                "latency_ms": 1234.5,
                "error": None,
                "timestamp": "2026-03-14T10:30:00.000Z",
            }
        }


class MetricsResponse(BaseModel):
    """Human-readable metrics summary."""
    total_requests: int
    successful_requests: int
    failed_requests: int
    success_rate: float
    average_latency_ms: float
    p95_latency_ms: float
    active_sessions: int
    queue_length: int
    recovery_events: int
    uptime_s: float

    class Config:
        json_schema_extra = {
            "example": {
                "total_requests": 1000,
                "successful_requests": 980,
                "failed_requests": 20,
                "success_rate": 0.98,
                "average_latency_ms": 1500.0,
                "p95_latency_ms": 5000.0,
                "active_sessions": 2,
                "queue_length": 3,
                "recovery_events": 5,
                "uptime_s": 86400.0,
            }
        }


class ErrorResponse(BaseModel):
    """Error response model."""
    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "error": "Internal Server Error",
                "detail": "No sessions available",
                "request_id": "req_abc123",
            }
        }
