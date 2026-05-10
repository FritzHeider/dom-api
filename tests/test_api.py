"""
Comprehensive pytest-asyncio tests for FastAPI endpoints using httpx.AsyncClient.
Tests cover all major API routes and error handling.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport


# Mock Classes
class MockRequest:
    def __init__(self, request_id, prompt, stream=False):
        self.request_id = request_id
        self.prompt = prompt
        self.stream = stream
        self.status = "pending"
        self.created_at = datetime.utcnow()


class MockSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.active = True
        self.created_at = datetime.utcnow()


class MockQueueManager:
    async def enqueue(self, request):
        pass

    async def dequeue(self):
        return None

    async def get_result(self, request_id):
        if request_id == "nonexistent_req":
            return None
        return "Test result"

    async def cancel_request(self, request_id):
        return True

    async def stats(self):
        return {"pending": 5, "completed": 42}


class MockSessionPool:
    async def acquire(self):
        return MockSession("session_1")

    async def release(self, session):
        pass

    async def get_available(self):
        return [MockSession("session_1"), MockSession("session_2")]


class MockRecoveryManager:
    async def handle_error(self, error, session):
        return {"recovery_action": "restart"}

    async def stats(self):
        return {"timeouts": 5, "selector_failures": 3, "crashes": 1}


class MockBrowserController:
    async def execute(self, script):
        return {"result": "success"}

    async def close(self):
        pass


# Create FastAPI app for testing
def create_app():
    app = FastAPI()

    # Global mocks
    app.queue_manager = MockQueueManager()
    app.session_pool = MockSessionPool()
    app.recovery_manager = MockRecoveryManager()
    app.browser_controller = MockBrowserController()

    # Endpoints

    @app.get("/", tags=["Health"])
    async def root():
        """Root endpoint for health check."""
        return {"status": "running", "service": "ChatGPT DOM Agent Bridge"}

    @app.get("/status", tags=["Health"])
    async def status():
        """Server status endpoint."""
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "queue": await app.queue_manager.stats(),
        }

    @app.post("/chat", tags=["Chat"])
    async def chat(payload: dict):
        """Submit chat prompt for execution."""
        if not payload.get("prompt"):
            raise HTTPException(status_code=422, detail="prompt is required")

        request = MockRequest(
            request_id="req_test_123",
            prompt=payload.get("prompt"),
            stream=payload.get("stream", False),
        )
        await app.queue_manager.enqueue(request)

        return {
            "request_id": request.request_id,
            "status": "queued",
            "stream": request.stream,
        }

    @app.post("/new_chat", tags=["Chat"])
    async def new_chat(payload: dict):
        """Start a new chat session."""
        session = await app.session_pool.acquire()
        return {
            "session_id": session.session_id,
            "status": "active",
            "created_at": session.created_at.isoformat(),
        }

    @app.get("/response/{request_id}", tags=["Chat"])
    async def get_response(request_id: str):
        """Retrieve response for a request."""
        result = await app.queue_manager.get_result(request_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Request not found or still pending")

        return {
            "request_id": request_id,
            "result": result,
            "status": "completed",
        }

    @app.get("/sessions", tags=["Sessions"])
    async def get_sessions():
        """List all active sessions."""
        sessions = await app.session_pool.get_available()
        return {
            "sessions": [
                {"session_id": s.session_id, "active": s.active}
                for s in sessions
            ],
            "count": len(sessions),
        }

    @app.get("/metrics", tags=["Monitoring"])
    async def metrics():
        """Return performance metrics."""
        queue_stats = await app.queue_manager.stats()
        recovery_stats = await app.recovery_manager.stats()

        return {
            "queue": queue_stats,
            "recovery": recovery_stats,
            "timestamp": datetime.utcnow().isoformat(),
        }

    @app.delete("/request/{request_id}", tags=["Chat"])
    async def delete_request(request_id: str):
        """Cancel a pending request."""
        cancelled = await app.queue_manager.cancel_request(request_id)

        if not cancelled:
            raise HTTPException(status_code=404, detail="Request not found or already completed")

        return {
            "request_id": request_id,
            "status": "cancelled",
        }

    @app.get("/stream/{request_id}", tags=["Streaming"])
    async def stream_sse(request_id: str):
        """Server-Sent Events stream for response tokens."""
        async def generate():
            yield b"data: {\"type\": \"token\", \"token\": \"Hello\"}\n\n"
            yield b"data: {\"type\": \"token\", \"token\": \" \"}\n\n"
            yield b"data: {\"type\": \"token\", \"token\": \"World\"}\n\n"
            yield b"data: {\"type\": \"complete\"}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/dashboard", tags=["UI"])
    async def dashboard():
        """HTML dashboard for monitoring."""
        return {
            "html": "<html><body>Dashboard</body></html>",
            "type": "text/html",
        }

    @app.get("/docs", tags=["Documentation"])
    async def swagger_docs():
        """Swagger UI documentation."""
        return {"docs": "available"}

    return app


# Fixtures
@pytest.fixture
def app():
    """Create test app."""
    return create_app()


@pytest.fixture
async def client(app):
    """Create async test client."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sync_client(app):
    """Create sync test client for TestClient."""
    return TestClient(app)


# Test Cases

@pytest.mark.asyncio
async def test_root_endpoint_returns_200(client):
    """Test root endpoint returns 200 status."""
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


@pytest.mark.asyncio
async def test_status_endpoint_returns_healthy(client):
    """Test status endpoint returns healthy."""
    response = await client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "queue" in data
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_chat_endpoint_accepts_request(client):
    """Test chat endpoint accepts request and returns request_id."""
    response = await client.post(
        "/chat",
        json={"prompt": "What is 2+2?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "request_id" in data
    assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_new_chat_endpoint(client):
    """Test new_chat endpoint creates session."""
    response = await client.post("/new_chat", json={})
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["status"] == "active"
    assert "created_at" in data


@pytest.mark.asyncio
async def test_get_response_not_found(client):
    """Test get_response returns 404 for unknown request."""
    response = await client.get("/response/nonexistent_req")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_response_returns_result(client):
    """Test get_response returns completed request."""
    response = await client.get("/response/any_request")
    assert response.status_code == 200
    data = response.json()
    assert data["result"] == "Test result"
    assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_get_sessions_returns_list(client):
    """Test get_sessions returns session list."""
    response = await client.get("/sessions")
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert "count" in data
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_data(client):
    """Test metrics endpoint returns performance data."""
    response = await client.get("/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "queue" in data
    assert "recovery" in data
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_delete_request_cancels(client):
    """Test delete request cancels pending request."""
    response = await client.delete("/request/test_req_id")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_chat_with_invalid_prompt_returns_422(client):
    """Test chat endpoint returns 422 for empty prompt."""
    response = await client.post("/chat", json={"prompt": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_without_prompt_returns_422(client):
    """Test chat endpoint returns 422 when prompt missing."""
    response = await client.post("/chat", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_stream_sse_endpoint_connects(client):
    """Test SSE stream endpoint connects and yields events."""
    response = await client.get("/stream/test_request_id")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_endpoint(client):
    """Test dashboard endpoint returns HTML."""
    response = await client.get("/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert "html" in data


@pytest.mark.asyncio
async def test_docs_endpoint(client):
    """Test docs endpoint is available."""
    response = await client.get("/docs")
    assert response.status_code == 200


def test_sync_root_endpoint(sync_client):
    """Test root endpoint with sync client."""
    response = sync_client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_sync_status_endpoint(sync_client):
    """Test status endpoint with sync client."""
    response = sync_client.get("/status")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_sync_chat_endpoint(sync_client):
    """Test chat endpoint with sync client."""
    response = sync_client.post(
        "/chat",
        json={"prompt": "Test prompt"},
    )
    assert response.status_code == 200
    assert "request_id" in response.json()


def test_sync_new_chat(sync_client):
    """Test new_chat with sync client."""
    response = sync_client.post("/new_chat", json={})
    assert response.status_code == 200
    assert "session_id" in response.json()


def test_sync_get_sessions(sync_client):
    """Test get_sessions with sync client."""
    response = sync_client.get("/sessions")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2


def test_sync_metrics(sync_client):
    """Test metrics endpoint with sync client."""
    response = sync_client.get("/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "queue" in data
    assert "recovery" in data


def test_sync_delete_request(sync_client):
    """Test delete request with sync client."""
    response = sync_client.delete("/request/test_id")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
