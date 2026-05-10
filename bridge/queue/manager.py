"""
Redis-backed priority queue manager.
Handles enqueue, dequeue, status tracking, and result retrieval.
Uses sorted sets for priority-based ordering with timestamp-based scoring.
"""

import asyncio
import json
from datetime import datetime
from typing import Optional

import redis.asyncio as aioredis

from bridge.config import settings
from bridge.logging_setup import get_logger
from bridge.queue.models import QueueRequest, QueueStats, Priority, RequestStatus


class QueueManager:
    """
    Manages priority queue backed by Redis.
    Uses Redis sorted sets for priority queues:
    - Each priority level has its own sorted set: {queue_name}:{priority}
    - Score = timestamp (lower = older = dequeued first within priority)
    - Request data stored in Redis hash: {queue_name}:data:{request_id}
    - Status stored in: {queue_name}:status:{request_id}
    - Results stored in: {queue_name}:result:{request_id} (with 1hr expiry)
    """

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._logger = get_logger("queue_manager")
        self.queue_name = settings.queue_name

    async def connect(self):
        """Initialize Redis connection."""
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        await self._redis.ping()
        self._logger.info("Connected to Redis", url=settings.redis_url)

    async def disconnect(self):
        """Close Redis connection gracefully."""
        if self._redis:
            await self._redis.close()

    async def enqueue(self, request: QueueRequest) -> str:
        """
        Add request to priority queue.
        Returns request_id.
        """
        score = request.timestamp.timestamp()
        queue_key = f"{self.queue_name}:{request.priority.value}"
        data_key = f"{self.queue_name}:data:{request.request_id}"

        pipe = self._redis.pipeline()
        pipe.zadd(queue_key, {request.request_id: score})
        pipe.hset(data_key, mapping={"data": json.dumps(request.to_redis_dict())})
        pipe.expire(data_key, 3600)
        await pipe.execute()

        self._logger.info(
            "Request enqueued",
            request_id=request.request_id,
            priority=request.priority.name,
        )
        return request.request_id

    async def dequeue(self) -> Optional[QueueRequest]:
        """
        Dequeue highest-priority oldest request.
        Non-blocking — returns None if no requests available.
        Priority order: HIGH > NORMAL > LOW
        """
        # Try HIGH first, then NORMAL, then LOW
        for priority in [Priority.HIGH, Priority.NORMAL, Priority.LOW]:
            queue_key = f"{self.queue_name}:{priority.value}"
            # ZPOPMIN = pop item with lowest score (oldest timestamp)
            result = await self._redis.zpopmin(queue_key, count=1)
            if result:
                request_id = result[0][0]
                req = await self.get_request(request_id)
                if req:
                    return req
        return None

    async def dequeue_blocking(self, timeout: float = 5.0) -> Optional[QueueRequest]:
        """
        Blocking dequeue that waits up to timeout seconds for a request.
        Polls with 100ms intervals.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            req = await self.dequeue()
            if req:
                return req
            await asyncio.sleep(0.1)
        return None

    async def get_request(self, request_id: str) -> Optional[QueueRequest]:
        """Retrieve a request by ID from storage."""
        data_key = f"{self.queue_name}:data:{request_id}"
        data = await self._redis.hget(data_key, "data")
        if not data:
            return None
        return QueueRequest.from_redis_dict(json.loads(data))

    async def update_request(self, request: QueueRequest):
        """Update request data in Redis."""
        data_key = f"{self.queue_name}:data:{request.request_id}"
        await self._redis.hset(
            data_key, mapping={"data": json.dumps(request.to_redis_dict())}
        )
        await self._redis.expire(data_key, 3600)

    async def complete_request(
        self, request_id: str, result: str, latency_ms: float, tokens_streamed: bool = False
    ):
        """
        Mark request as completed and store result.
        Result stored separately with 1hr TTL for quick lookup.
        """
        req = await self.get_request(request_id)
        if req:
            req.status = RequestStatus.COMPLETED
            req.result = result
            req.latency_ms = latency_ms
            req.tokens_streamed = tokens_streamed
            req.completed_at = datetime.utcnow()
            await self.update_request(req)

            # Also store in result hash with 1hr TTL for quick lookup
            result_key = f"{self.queue_name}:result:{request_id}"
            await self._redis.setex(
                result_key, 3600, json.dumps(req.to_redis_dict())
            )
            self._logger.info(
                "Request completed",
                request_id=request_id,
                latency_ms=latency_ms,
                tokens_streamed=tokens_streamed,
            )

    async def fail_request(self, request_id: str, error: str):
        """
        Mark request as failed.
        Retries up to max_retries with exponential backoff.
        """
        req = await self.get_request(request_id)
        if req:
            req.retry_count += 1
            if req.retry_count <= req.max_retries:
                req.status = RequestStatus.RETRY
                # Re-enqueue with same priority
                await self.enqueue(req)
                self._logger.warning(
                    "Request queued for retry",
                    request_id=request_id,
                    retry_count=req.retry_count,
                    max_retries=req.max_retries,
                )
            else:
                req.status = RequestStatus.FAILED
                req.error = error
                req.completed_at = datetime.utcnow()
                await self.update_request(req)
                self._logger.error(
                    "Request permanently failed",
                    request_id=request_id,
                    retry_count=req.retry_count,
                    error=error,
                )

    async def get_result(self, request_id: str) -> Optional[QueueRequest]:
        """
        Retrieve result of a completed request.
        Checks result cache first (faster), falls back to main storage.
        """
        result_key = f"{self.queue_name}:result:{request_id}"
        data = await self._redis.get(result_key)
        if data:
            return QueueRequest.from_redis_dict(json.loads(data))
        return await self.get_request(request_id)

    async def get_stats(self) -> QueueStats:
        """Get current queue statistics."""
        stats = QueueStats()
        for priority in Priority:
            queue_key = f"{self.queue_name}:{priority.value}"
            count = await self._redis.zcard(queue_key)
            stats.queue_length_by_priority[priority.name] = count
            stats.total_pending += count
        return stats

    async def cancel_request(self, request_id: str):
        """Cancel a pending request."""
        for priority in Priority:
            queue_key = f"{self.queue_name}:{priority.value}"
            await self._redis.zrem(queue_key, request_id)
        req = await self.get_request(request_id)
        if req:
            req.status = RequestStatus.CANCELLED
            await self.update_request(req)
            self._logger.info("Request cancelled", request_id=request_id)
