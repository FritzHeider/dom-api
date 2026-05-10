"""
Pydantic v2 models for the queue system.
Handles request serialization, status tracking, and result storage.
"""

from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional
import uuid
from datetime import datetime


class Priority(int, Enum):
    """Request priority levels."""
    LOW = 0
    NORMAL = 1
    HIGH = 2


class RequestStatus(str, Enum):
    """Request lifecycle states."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"
    CANCELLED = "cancelled"


class QueueRequest(BaseModel):
    """Request model for queue system with full serialization support."""

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    prompt: str
    conversation_id: Optional[str] = None
    new_chat: bool = False
    session_id: Optional[str] = None
    priority: Priority = Priority.NORMAL
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    retry_count: int = 0
    max_retries: int = 3
    status: RequestStatus = RequestStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    tokens_streamed: bool = False
    latency_ms: Optional[float] = None
    completed_at: Optional[datetime] = None
    stream_tokens: list[str] = Field(default_factory=list)

    model_config = {
        "use_enum_values": False,
    }

    def to_redis_dict(self) -> dict:
        """Convert to JSON-serializable dict for Redis storage."""
        return {
            "request_id": self.request_id,
            "prompt": self.prompt,
            "conversation_id": self.conversation_id,
            "new_chat": self.new_chat,
            "session_id": self.session_id,
            "priority": self.priority.value,
            "timestamp": self.timestamp.isoformat(),
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "tokens_streamed": self.tokens_streamed,
            "latency_ms": self.latency_ms,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "stream_tokens": self.stream_tokens,
        }

    @classmethod
    def from_redis_dict(cls, data: dict) -> "QueueRequest":
        """Parse from Redis dict with proper type conversion."""
        # Convert ISO format strings back to datetime
        if isinstance(data.get("timestamp"), str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        if data.get("completed_at") and isinstance(data["completed_at"], str):
            data["completed_at"] = datetime.fromisoformat(data["completed_at"])

        # Convert priority value to enum
        if isinstance(data.get("priority"), int):
            data["priority"] = Priority(data["priority"])

        # Convert status value to enum
        if isinstance(data.get("status"), str):
            data["status"] = RequestStatus(data["status"])

        return cls(**data)


class QueueStats(BaseModel):
    """Queue statistics model."""

    total_pending: int = 0
    total_processing: int = 0
    total_completed: int = 0
    total_failed: int = 0
    queue_length_by_priority: dict[str, int] = Field(default_factory=dict)
    average_latency_ms: float = 0.0
    success_rate: float = 0.0
