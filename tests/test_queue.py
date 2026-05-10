"""
Comprehensive pytest-asyncio tests for the queue system using fakeredis.
Tests cover enqueue, dequeue, priority ordering, retry logic, and persistence.
"""

import json
import pytest
import fakeredis.aioredis
from unittest.mock import patch, AsyncMock
from datetime import datetime, timedelta
from enum import Enum

# Test enums and types
class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class RequestStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Request:
    """Request model for testing."""
    def __init__(self, request_id, prompt, priority=Priority.NORMAL, max_retries=3):
        self.request_id = request_id
        self.prompt = prompt
        self.priority = priority
        self.status = RequestStatus.PENDING
        self.max_retries = max_retries
        self.retry_count = 0
        self.created_at = datetime.utcnow()
        self.result = None
        self.error = None

    def to_dict(self):
        return {
            "request_id": self.request_id,
            "prompt": self.prompt,
            "priority": self.priority.value,
            "status": self.status.value,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat(),
            "result": self.result,
            "error": self.error,
        }

    @staticmethod
    def from_dict(data):
        req = Request(
            request_id=data["request_id"],
            prompt=data["prompt"],
            priority=Priority(data.get("priority", "normal")),
            max_retries=data.get("max_retries", 3),
        )
        req.status = RequestStatus(data["status"])
        req.retry_count = data.get("retry_count", 0)
        req.result = data.get("result")
        req.error = data.get("error")
        return req


class QueueManager:
    """Manages request queue with priority support and Redis persistence."""

    PRIORITY_ORDER = {Priority.HIGH: 0, Priority.NORMAL: 1, Priority.LOW: 2}

    def __init__(self, redis_client):
        self.redis = redis_client
        self.requests = {}  # In-memory cache

    async def enqueue(self, request: Request) -> None:
        """Add request to queue with priority."""
        self.requests[request.request_id] = request
        request.status = RequestStatus.PENDING

        # Store in Redis with priority score
        score = self.PRIORITY_ORDER[request.priority]
        await self.redis.zadd(
            "queue:requests",
            {request.request_id: score},
        )
        await self.redis.set(
            f"request:{request.request_id}",
            json.dumps(request.to_dict()),
        )

    async def dequeue(self, blocking=False, timeout=None) -> Request | None:
        """Dequeue highest priority request."""
        # Pop lowest score (highest priority)
        result = await self.redis.zpopmin("queue:requests", count=1)

        if not result:
            return None

        request_id = result[0][0].decode() if isinstance(result[0][0], bytes) else result[0][0]

        # Retrieve request data
        data = await self.redis.get(f"request:{request_id}")
        if not data:
            return None

        request = Request.from_dict(json.loads(data))
        request.status = RequestStatus.PROCESSING

        # Update in Redis
        await self.redis.set(
            f"request:{request_id}",
            json.dumps(request.to_dict()),
        )

        return request

    async def fail_request(self, request_id: str) -> bool:
        """Increment retry count and re-enqueue if under max retries."""
        data = await self.redis.get(f"request:{request_id}")
        if not data:
            return False

        request = Request.from_dict(json.loads(data))
        request.retry_count += 1

        if request.retry_count > request.max_retries:
            request.status = RequestStatus.FAILED
            await self.redis.set(
                f"request:{request_id}",
                json.dumps(request.to_dict()),
            )
            return False

        # Re-enqueue
        request.status = RequestStatus.PENDING
        score = self.PRIORITY_ORDER[request.priority]
        await self.redis.zadd("queue:requests", {request_id: score})
        await self.redis.set(
            f"request:{request_id}",
            json.dumps(request.to_dict()),
        )
        return True

    async def complete_request(self, request_id: str, result: str) -> None:
        """Mark request as completed with result."""
        data = await self.redis.get(f"request:{request_id}")
        if not data:
            return

        request = Request.from_dict(json.loads(data))
        request.status = RequestStatus.COMPLETED
        request.result = result

        await self.redis.set(
            f"request:{request_id}",
            json.dumps(request.to_dict()),
        )
        await self.redis.zadd("queue:completed", {request_id: 1})

    async def get_result(self, request_id: str) -> str | None:
        """Retrieve completed request result."""
        data = await self.redis.get(f"request:{request_id}")
        if not data:
            return None

        request = Request.from_dict(json.loads(data))
        if request.status == RequestStatus.COMPLETED:
            return request.result

        return None

    async def cancel_request(self, request_id: str) -> bool:
        """Cancel a pending request."""
        data = await self.redis.get(f"request:{request_id}")
        if not data:
            return False

        request = Request.from_dict(json.loads(data))
        if request.status not in [RequestStatus.PENDING, RequestStatus.PROCESSING]:
            return False

        request.status = RequestStatus.CANCELLED
        await self.redis.set(
            f"request:{request_id}",
            json.dumps(request.to_dict()),
        )
        await self.redis.zrem("queue:requests", request_id)
        return True

    async def stats(self) -> dict:
        """Return queue statistics."""
        pending_count = await self.redis.zcard("queue:requests")
        completed_count = await self.redis.zcard("queue:completed")

        return {
            "pending": pending_count,
            "completed": completed_count,
        }


# Fixtures
@pytest.fixture
async def redis_client():
    """Create fakeredis client for testing."""
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.flushall()


@pytest.fixture
async def queue_manager(redis_client):
    """Create queue manager with mocked Redis."""
    return QueueManager(redis_client)


# Test Cases

@pytest.mark.asyncio
async def test_enqueue_and_dequeue_normal_priority(queue_manager):
    """Test basic enqueue and dequeue operations."""
    request = Request("req1", "Hello ChatGPT", priority=Priority.NORMAL)
    await queue_manager.enqueue(request)

    dequeued = await queue_manager.dequeue()
    assert dequeued is not None
    assert dequeued.request_id == "req1"
    assert dequeued.prompt == "Hello ChatGPT"
    assert dequeued.status == RequestStatus.PROCESSING


@pytest.mark.asyncio
async def test_priority_ordering(queue_manager):
    """Test that HIGH priority is dequeued before NORMAL before LOW."""
    low = Request("low1", "Low priority", priority=Priority.LOW)
    normal = Request("normal1", "Normal priority", priority=Priority.NORMAL)
    high = Request("high1", "High priority", priority=Priority.HIGH)

    # Enqueue in reverse order
    await queue_manager.enqueue(low)
    await queue_manager.enqueue(normal)
    await queue_manager.enqueue(high)

    # Should dequeue in priority order
    first = await queue_manager.dequeue()
    assert first.request_id == "high1"

    second = await queue_manager.dequeue()
    assert second.request_id == "normal1"

    third = await queue_manager.dequeue()
    assert third.request_id == "low1"


@pytest.mark.asyncio
async def test_retry_on_failure(queue_manager):
    """Test that failed requests are re-enqueued up to max_retries."""
    request = Request("req1", "Test", max_retries=3)
    await queue_manager.enqueue(request)

    # First attempt
    await queue_manager.dequeue()
    should_retry = await queue_manager.fail_request("req1")
    assert should_retry is True

    # Second attempt
    await queue_manager.dequeue()
    should_retry = await queue_manager.fail_request("req1")
    assert should_retry is True

    # Third attempt
    await queue_manager.dequeue()
    should_retry = await queue_manager.fail_request("req1")
    assert should_retry is True

    # Fourth attempt - should fail permanently
    await queue_manager.dequeue()
    should_retry = await queue_manager.fail_request("req1")
    assert should_retry is False


@pytest.mark.asyncio
async def test_permanent_failure_after_max_retries(queue_manager):
    """Test that request is marked FAILED after max retries exceeded."""
    request = Request("req1", "Test", max_retries=1)
    await queue_manager.enqueue(request)

    # Fail twice
    await queue_manager.dequeue()
    await queue_manager.fail_request("req1")

    await queue_manager.dequeue()
    should_retry = await queue_manager.fail_request("req1")

    assert should_retry is False

    # Verify status is FAILED
    data = await queue_manager.redis.get("request:req1")
    request = Request.from_dict(json.loads(data))
    assert request.status == RequestStatus.FAILED


@pytest.mark.asyncio
async def test_complete_request_stores_result(queue_manager):
    """Test that completed requests store their result."""
    request = Request("req1", "What is 2+2?")
    await queue_manager.enqueue(request)
    await queue_manager.dequeue()

    result = "2+2 equals 4"
    await queue_manager.complete_request("req1", result)

    data = await queue_manager.redis.get("request:req1")
    completed_request = Request.from_dict(json.loads(data))
    assert completed_request.status == RequestStatus.COMPLETED
    assert completed_request.result == result


@pytest.mark.asyncio
async def test_get_result_returns_completed_request(queue_manager):
    """Test retrieving result from completed request."""
    request = Request("req1", "Test prompt")
    await queue_manager.enqueue(request)
    await queue_manager.dequeue()

    expected_result = "Test result"
    await queue_manager.complete_request("req1", expected_result)

    result = await queue_manager.get_result("req1")
    assert result == expected_result


@pytest.mark.asyncio
async def test_get_result_returns_none_for_pending(queue_manager):
    """Test that pending requests return None."""
    request = Request("req1", "Test prompt")
    await queue_manager.enqueue(request)

    result = await queue_manager.get_result("req1")
    assert result is None


@pytest.mark.asyncio
async def test_cancel_request_removes_from_queue(queue_manager):
    """Test cancelling a pending request."""
    request = Request("req1", "Test")
    await queue_manager.enqueue(request)

    cancelled = await queue_manager.cancel_request("req1")
    assert cancelled is True

    # Should not dequeue cancelled request
    dequeued = await queue_manager.dequeue()
    assert dequeued is None


@pytest.mark.asyncio
async def test_queue_stats_returns_correct_counts(queue_manager):
    """Test queue statistics are accurate."""
    req1 = Request("req1", "Test 1")
    req2 = Request("req2", "Test 2")
    req3 = Request("req3", "Test 3")

    await queue_manager.enqueue(req1)
    await queue_manager.enqueue(req2)
    await queue_manager.enqueue(req3)

    stats = await queue_manager.stats()
    assert stats["pending"] == 3

    await queue_manager.dequeue()
    await queue_manager.complete_request("req1", "Result")

    stats = await queue_manager.stats()
    assert stats["pending"] == 2
    assert stats["completed"] == 1


@pytest.mark.asyncio
async def test_dequeue_blocking_returns_none_on_empty(queue_manager):
    """Test dequeue on empty queue returns None."""
    result = await queue_manager.dequeue(blocking=False)
    assert result is None


@pytest.mark.asyncio
async def test_request_serialization_roundtrip(queue_manager):
    """Test that requests survive JSON serialization roundtrip."""
    original = Request(
        "req1",
        "Complex prompt with special chars: !@#$%",
        priority=Priority.HIGH,
        max_retries=5,
    )
    original.retry_count = 2

    await queue_manager.enqueue(original)

    # Retrieve and verify
    data = await queue_manager.redis.get("request:req1")
    restored = Request.from_dict(json.loads(data))

    assert restored.request_id == original.request_id
    assert restored.prompt == original.prompt
    assert restored.priority == original.priority
    assert restored.max_retries == original.max_retries
    assert restored.retry_count == original.retry_count
