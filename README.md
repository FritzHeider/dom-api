# ChatGPT DOM Agent Bridge

A production-ready FastAPI service that bridges to ChatGPT's web interface, enabling programmatic interaction with GPT models through DOM automation. The system manages browser sessions, handles request queuing with priority support, provides real-time streaming responses via SSE and WebSocket, and includes comprehensive error recovery mechanisms.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        External Clients                          │
│              (REST API, WebSocket, SSE Streams)                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                      FastAPI Server                              │
│  ┌─────────────┬──────────────┬──────────────┬──────────────┐   │
│  │ /chat       │ /new_chat    │ /response    │ /stream      │   │
│  │ /sessions   │ /metrics     │ /dashboard   │ /status      │   │
│  └──────┬──────┴──────┬───────┴──────┬───────┴──────┬───────┘   │
└─────────┼─────────────┼──────────────┼──────────────┼────────────┘
          │             │              │              │
┌─────────▼──────────────▼──────────────▼──────────────▼────────────┐
│                    Queue Manager (Redis)                           │
│  • Priority queuing (HIGH/NORMAL/LOW)                             │
│  • Request persistence                                            │
│  • Result storage                                                 │
└─────────────────────────┬────────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────────┐
│                    Session Pool                                    │
│  • Browser session lifecycle management                           │
│  • Connection pooling                                             │
│  • Idle timeout handling                                          │
└─────────┬─────────────────────────────────────────────┬──────────┘
          │                                             │
┌─────────▼──────────────────────┐     ┌───────────────▼─────────┐
│   Browser Controller             │     │   Recovery Manager      │
│  (Playwright)                    │     │                         │
│  • DOM interaction               │     │  • Error classification │
│  • Network interception          │     │  • Recovery actions     │
│  • React state inspection        │     │  • Session health check │
│  • Screenshot capture            │     │  • Failure tracking     │
└──────┬──────────────────────────┘     └───────────┬─────────────┘
       │                                            │
       ├─────────────────────────────────────────────┤
       │                                             │
┌──────▼──────────────────────────────────────┐     │
│     Network Interceptor                      │     │
│  • HTTP request monitoring                   │     │
│  • Response capture                          │     │
│  • Rate limiting                             │     │
└──────────────────────────────────────────────┘     │
                                                     │
┌────────────────────────────────────────────────────▼───┐
│            Selector System                              │
│  • DOM selector discovery                              │
│  • Confidence scoring                                  │
│  • Failure tracking and self-healing                   │
│  • Persistent selector storage                         │
└────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│            Streaming System                              │
│  • SSE event manager                                    │
│  • WebSocket handler                                    │
│  • Multi-subscriber support                            │
│  • Token buffering and delivery                         │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│            Monitoring                                    │
│  • Prometheus metrics export                            │
│  • Performance tracking                                 │
│  • Error rate monitoring                                │
│  • Queue depth monitoring                               │
└─────────────────────────────────────────────────────────┘
```

## Module Breakdown

### browser_controller.py
Manages Playwright browser instances and DOM interactions. Handles page navigation, element selection, JavaScript execution, network interception, and screenshot capture. Provides high-level API for ChatGPT interaction including typing prompts, clicking send button, and reading responses from the page.

### network_interceptor.py
Intercepts HTTP requests and responses during browser execution. Captures API calls made by ChatGPT's frontend to understand data flow and response format. Enables monitoring of rate limits and detecting network errors that affect response generation.

### react_state_reader.py
Inspects React Fiber tree to extract component state directly from memory. Provides access to ChatGPT's internal state without DOM scraping, improving reliability. Detects when responses are ready, handles incomplete responses, and monitors component lifecycle.

### ui_fallback.py
Implements fallback strategies when modern APIs fail. Falls back from React state inspection to DOM scraping when necessary. Handles ChatGPT UI changes gracefully by trying multiple selector variants. Ensures system resilience during ChatGPT frontend updates.

### selector_system.py
Discovers and manages DOM selectors with confidence scoring. Stores selectors persistently and tracks failure rates. Automatically rediscovers selectors when failures occur. Maintains multiple selector candidates for the same element to handle UI variations.

### queue.py
Implements Redis-backed request queue with priority support (HIGH/NORMAL/LOW). Manages request lifecycle from enqueue through completion. Handles retries with exponential backoff. Stores request metadata and results persistently.

### session_pool.py
Manages pool of browser sessions with lifecycle management. Handles session creation, reuse, and cleanup. Tracks session health and idle timeout. Implements concurrent session acquisition and release with backpressure handling.

### prompt_engine.py
Constructs and executes prompts in ChatGPT sessions. Manages chat message flow and response parsing. Handles special commands and formatting. Integrates with network interceptor to capture API responses.

### recovery.py
Implements error recovery and session health management. Classifies errors and determines appropriate recovery actions (retry, reload, restart). Tracks recovery statistics and failure patterns. Automatically heals from transient errors.

### api.py
FastAPI application with REST endpoints, SSE streaming, and WebSocket support. Handles request submission, response retrieval, session management, and real-time streaming. Provides metrics and monitoring endpoints. Implements proper HTTP status codes and error handling.

### monitoring.py
Exports Prometheus metrics for performance monitoring. Tracks queue depth, response latency, error rates, session health, and recovery metrics. Enables observability into system behavior and capacity planning.

## Quick Start (5 Steps)

### 1. Clone and Setup
```bash
git clone https://github.com/yourusername/chatgpt-dom-bridge.git
cd chatgpt-dom-bridge
```

### 2. Install Dependencies
```bash
bash scripts/setup.sh
```

### 3. Start Redis
```bash
# Using Docker (recommended)
docker run -d -p 6379:6379 redis:7-alpine

# Or using Homebrew
redis-server

# Or using system package manager
sudo systemctl start redis-server
```

### 4. Configure Environment
```bash
# Edit .env and add your ChatGPT credentials
vim .env

# Required variables:
# CHATGPT_EMAIL=your-email@example.com
# CHATGPT_PASSWORD=your-password
```

### 5. Run the Server
```bash
bash scripts/start.sh
```

The API will be available at `http://localhost:8000` with documentation at `http://localhost:8000/docs`.

## Setup Instructions

### System Requirements

- **Python**: 3.11 or higher
- **Redis**: 5.0 or higher (local or Docker)
- **Disk Space**: 1GB minimum for browser cache and logs
- **RAM**: 2GB minimum (4GB+ recommended for multiple sessions)
- **Network**: Stable internet connection to access ChatGPT

### Step-by-Step Installation

#### 1. Verify Python Installation
```bash
python3 --version  # Should be 3.11+
```

#### 2. Clone Repository
```bash
git clone https://github.com/yourusername/chatgpt-dom-bridge.git
cd chatgpt-dom-bridge
```

#### 3. Run Setup Script
```bash
bash scripts/setup.sh
```

This script will automatically:
- Check Python version
- Create Python virtual environment
- Install pip dependencies
- Install Playwright and browser drivers
- Create necessary directories
- Generate sample .env file

#### 4. Install and Start Redis

**Using Docker (Recommended):**
```bash
docker run -d \
  --name redis-bridge \
  -p 6379:6379 \
  redis:7-alpine
```

**Using Homebrew (macOS):**
```bash
brew install redis
redis-server
```

**Using apt (Ubuntu/Debian):**
```bash
sudo apt-get install redis-server
sudo systemctl start redis-server
```

**From Source:**
```bash
wget http://download.redis.io/redis-stable.tar.gz
tar xzf redis-stable.tar.gz
cd redis-stable
make
sudo make install
redis-server
```

#### 5. Configure Credentials

Edit `.env` in the project root:
```bash
vim .env
```

Update these required fields:
```bash
# ChatGPT Login Credentials
CHATGPT_EMAIL=your-email@example.com
CHATGPT_PASSWORD=your-password

# Optional: API Configuration
API_HOST=0.0.0.0
API_PORT=8000
SESSION_POOL_MAX=5
RESPONSE_WAIT_TIMEOUT_S=120
```

#### 6. Start the Server
```bash
bash scripts/start.sh
```

You should see output like:
```
========================================
ChatGPT DOM Agent Bridge
========================================
✓ Python 3.11.0 found
✓ Redis connection successful
✓ Virtual environment activated
========================================
Starting ChatGPT DOM Agent Bridge
========================================
INFO:     Started server process [12345]
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## API Reference

### Core Chat Endpoints

#### POST /chat
Submit a prompt to ChatGPT for execution.

**Request:**
```json
{
  "prompt": "What is 2+2?",
  "stream": false
}
```

**Response:**
```json
{
  "request_id": "req_abc123",
  "status": "queued",
  "stream": false
}
```

**Status Codes:**
- `200`: Request accepted and queued
- `422`: Invalid request (missing prompt or empty prompt)

---

#### POST /new_chat
Start a new ChatGPT chat session.

**Request:**
```json
{}
```

**Response:**
```json
{
  "session_id": "sess_xyz789",
  "status": "active",
  "created_at": "2024-01-15T10:30:45Z"
}
```

**Status Codes:**
- `200`: Session created successfully
- `503`: No sessions available in pool

---

#### GET /response/{request_id}
Retrieve the response for a completed request.

**Response:**
```json
{
  "request_id": "req_abc123",
  "result": "2+2 equals 4",
  "status": "completed"
}
```

**Status Codes:**
- `200`: Response available
- `404`: Request not found or still pending
- `202`: Request still processing (in some implementations)

---

#### DELETE /request/{request_id}
Cancel a pending request.

**Response:**
```json
{
  "request_id": "req_abc123",
  "status": "cancelled"
}
```

**Status Codes:**
- `200`: Request cancelled
- `404`: Request not found or already completed

---

### Session Management Endpoints

#### GET /sessions
List all active sessions.

**Response:**
```json
{
  "sessions": [
    {"session_id": "sess_1", "active": true},
    {"session_id": "sess_2", "active": true}
  ],
  "count": 2
}
```

**Status Codes:**
- `200`: List retrieved

---

#### GET /status
Check server health status.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:45Z",
  "queue": {
    "pending": 3,
    "completed": 42
  }
}
```

**Status Codes:**
- `200`: Server is healthy

---

### Monitoring and Metrics

#### GET /metrics
Get performance metrics and statistics.

**Response:**
```json
{
  "queue": {
    "pending": 3,
    "completed": 42
  },
  "recovery": {
    "timeouts": 2,
    "selector_failures": 1,
    "crashes": 0
  },
  "timestamp": "2024-01-15T10:30:45Z"
}
```

**Status Codes:**
- `200`: Metrics retrieved

---

### Streaming Endpoints

#### GET /stream/{request_id}
Server-Sent Events stream for real-time response tokens.

**Response Stream:**
```
data: {"type": "token", "token": "Hello"}

data: {"type": "token", "token": " "}

data: {"type": "token", "token": "World"}

data: {"type": "complete"}

```

**Status Codes:**
- `200`: Stream connected
- `404`: Request not found

---

#### WS /ws/stream/{request_id}
WebSocket endpoint for real-time streaming (alternative to SSE).

**Message Format:**
```json
{"type": "token", "token": "Hello"}
{"type": "token", "token": " World"}
{"type": "complete"}
```

**Status Codes:**
- `101`: WebSocket upgrade successful
- `404`: Request not found

---

### Dashboard and Documentation

#### GET /dashboard
HTML dashboard for real-time monitoring.

**Response:** HTML page with live metrics, queue status, and session info.

---

#### GET /docs
Swagger UI API documentation (auto-generated by FastAPI).

**Response:** Interactive API documentation with try-it-out functionality.

---

#### GET /
Root endpoint for health check.

**Response:**
```json
{
  "status": "running",
  "service": "ChatGPT DOM Agent Bridge"
}
```

---

## Streaming Guide

### Server-Sent Events (SSE)

SSE is the simplest approach for unidirectional server-to-client streaming. The server sends events continuously, and the browser/client automatically reconnects if disconnected.

#### Python with httpx

```python
import httpx
import json

def stream_chatgpt_response(prompt: str):
    """Stream response tokens from ChatGPT."""

    # 1. Submit request
    with httpx.Client() as client:
        response = client.post(
            "http://localhost:8000/chat",
            json={
                "prompt": prompt,
                "stream": True
            }
        )
        request_id = response.json()["request_id"]

    # 2. Stream response
    with httpx.stream("GET", f"http://localhost:8000/stream/{request_id}") as stream:
        full_response = ""

        for line in stream.iter_lines():
            if line.startswith("data:"):
                # Remove "data: " prefix and parse JSON
                json_str = line[5:].strip()
                if json_str:
                    event = json.loads(json_str)

                    if event["type"] == "token":
                        token = event["token"]
                        full_response += token
                        print(token, end="", flush=True)

                    elif event["type"] == "complete":
                        print("\n[Complete]")
                        break

                    elif event["type"] == "error":
                        print(f"\n[Error] {event.get('error', 'Unknown error')}")
                        break

        return full_response


# Usage
response = stream_chatgpt_response("What is machine learning?")
print(f"\nFinal response: {response}")
```

#### JavaScript in Browser

```javascript
async function streamChatGPTResponse(prompt) {
    // Submit request
    const chatResponse = await fetch('http://localhost:8000/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, stream: true })
    });
    const { request_id } = await chatResponse.json();

    // Stream response
    const streamResponse = await fetch(`http://localhost:8000/stream/${request_id}`);
    const reader = streamResponse.body.getReader();
    const decoder = new TextDecoder();
    let fullResponse = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
            if (line.startsWith('data:')) {
                const jsonStr = line.substring(5).trim();
                if (jsonStr) {
                    const event = JSON.parse(jsonStr);

                    if (event.type === 'token') {
                        fullResponse += event.token;
                        process.stdout.write(event.token);
                    } else if (event.type === 'complete') {
                        console.log('\n[Complete]');
                        return fullResponse;
                    } else if (event.type === 'error') {
                        console.error(`[Error] ${event.error}`);
                        return fullResponse;
                    }
                }
            }
        }
    }
    return fullResponse;
}

// Usage
streamChatGPTResponse('Tell me a joke').then(response => {
    console.log(`\nFinal response: ${response}`);
});
```

---

### WebSocket Streaming

WebSocket is better for bidirectional communication and provides lower latency. However, for simple streaming, SSE is often simpler.

#### Python with websockets

```python
import asyncio
import websockets
import json


async def stream_chatgpt_websocket(prompt: str):
    """Stream response using WebSocket."""
    import httpx

    # First submit the request via REST API
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/chat",
            json={"prompt": prompt, "stream": True}
        )
        request_id = response.json()["request_id"]

    # Connect to WebSocket
    async with websockets.connect(
        f"ws://localhost:8000/ws/stream/{request_id}"
    ) as websocket:
        full_response = ""

        async for message in websocket:
            data = json.loads(message)

            if data["type"] == "token":
                token = data["token"]
                full_response += token
                print(token, end="", flush=True)

            elif data["type"] == "complete":
                print("\n[Complete]")
                break

            elif data["type"] == "error":
                print(f"\n[Error] {data.get('error', 'Unknown error')}")
                break

        return full_response


# Usage
response = asyncio.run(stream_chatgpt_websocket("Hello, ChatGPT!"))
print(f"Response: {response}")
```

#### JavaScript with Native WebSocket

```javascript
function streamChatGPTWebSocket(prompt) {
    return new Promise(async (resolve) => {
        // Submit request first
        const chatResponse = await fetch('http://localhost:8000/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt, stream: true })
        });
        const { request_id } = await chatResponse.json();

        // Connect WebSocket
        const ws = new WebSocket(`ws://localhost:8000/ws/stream/${request_id}`);
        let fullResponse = '';

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);

            if (data.type === 'token') {
                fullResponse += data.token;
                process.stdout.write(data.token);
            } else if (data.type === 'complete') {
                console.log('\n[Complete]');
                ws.close();
                resolve(fullResponse);
            } else if (data.type === 'error') {
                console.error(`[Error] ${data.error}`);
                ws.close();
                resolve(fullResponse);
            }
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            resolve(fullResponse);
        };
    });
}

// Usage
streamChatGPTWebSocket('Write a haiku').then(response => {
    console.log(`\nFinal: ${response}`);
});
```

---

## Configuration Reference

All configuration is managed through environment variables in the `.env` file. Here's the complete reference:

### API Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | API server bind address |
| `API_PORT` | `8000` | API server port |
| `API_LOG_LEVEL` | `INFO` | Uvicorn log level (INFO, DEBUG, WARNING, ERROR) |

### Browser Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BROWSER_HEADLESS` | `true` | Run browser in headless mode |
| `BROWSER_SLOW_MO_MS` | `0` | Add delay (ms) to each browser action for debugging |
| `BROWSER_TIMEOUT_MS` | `30000` | Global browser operation timeout (ms) |
| `BROWSER_USER_AGENT` | (auto) | Custom user agent string |

### Session Management

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_POOL_MAX` | `5` | Maximum concurrent browser sessions |
| `SESSION_IDLE_TIMEOUT_S` | `300` | Idle session timeout (seconds) |
| `SESSION_RESTART_ON_ERROR` | `true` | Auto-restart session on unrecoverable error |

### Request Processing

| Variable | Default | Description |
|----------|---------|-------------|
| `RESPONSE_WAIT_TIMEOUT_S` | `120` | Max time to wait for response (seconds) |
| `MAX_RETRIES` | `3` | Maximum retry attempts for failed requests |
| `QUEUE_MAX_SIZE` | `1000` | Maximum requests to keep in Redis queue |

### Redis Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `REDIS_POOL_SIZE` | `10` | Redis connection pool size |

### ChatGPT Credentials

| Variable | Default | Description |
|----------|---------|-------------|
| `CHATGPT_EMAIL` | (required) | ChatGPT account email |
| `CHATGPT_PASSWORD` | (required) | ChatGPT account password |
| `CHATGPT_AUTH_METHOD` | `password` | Authentication method (password, sso, oauth) |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Application log level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FILE` | `logs/bridge.log` | Log file path |
| `LOG_FORMAT` | (standard) | Python logging format string |

### Debug and Development

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG_MODE` | `false` | Enable debug mode (verbose logging, no optimization) |
| `SAVE_SCREENSHOTS` | `false` | Save browser screenshots on errors |
| `SCREENSHOT_DIR` | `screenshots/` | Directory for screenshots |

### Example Configuration

```bash
# .env file example
API_HOST=0.0.0.0
API_PORT=8000
API_LOG_LEVEL=INFO

BROWSER_HEADLESS=true
BROWSER_TIMEOUT_MS=30000
SESSION_POOL_MAX=5
SESSION_IDLE_TIMEOUT_S=300

RESPONSE_WAIT_TIMEOUT_S=120
MAX_RETRIES=3

REDIS_URL=redis://localhost:6379/0

CHATGPT_EMAIL=user@example.com
CHATGPT_PASSWORD=your-secure-password

LOG_LEVEL=INFO
LOG_FILE=logs/bridge.log

DEBUG_MODE=false
```

---

## Deployment Instructions

### Local Development

#### With Virtual Environment

```bash
# Setup
bash scripts/setup.sh

# Start Redis
redis-server

# In another terminal, start the server
source venv/bin/activate
python -m uvicorn bridge.main:app --reload
```

#### With Docker Compose

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

  bridge:
    build: .
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://redis:6379/0
      - CHATGPT_EMAIL=${CHATGPT_EMAIL}
      - CHATGPT_PASSWORD=${CHATGPT_PASSWORD}
      - API_HOST=0.0.0.0
      - API_PORT=8000
    depends_on:
      - redis
    volumes:
      - ./logs:/app/logs
      - ./screenshots:/app/screenshots

volumes:
  redis_data:
```

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application
COPY . .

# Expose port
EXPOSE 8000

# Start server
CMD ["python", "-m", "uvicorn", "bridge.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Deploy:

```bash
docker-compose up -d
```

---

### Manual Production Deployment

#### 1. Prepare Server

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install system dependencies
sudo apt-get install -y \
    python3.11 \
    python3.11-venv \
    redis-server \
    nginx \
    supervisor

# Create application user
sudo useradd -m -d /opt/chatgpt-bridge chatgpt-bridge
```

#### 2. Deploy Application

```bash
# Clone to /opt/chatgpt-bridge
sudo -u chatgpt-bridge git clone https://github.com/yourusername/chatgpt-dom-bridge.git /opt/chatgpt-bridge

cd /opt/chatgpt-bridge

# Setup
sudo -u chatgpt-bridge bash scripts/setup.sh

# Create .env with credentials
sudo -u chatgpt-bridge vim .env
```

#### 3. Configure Supervisor

Create `/etc/supervisor/conf.d/chatgpt-bridge.conf`:

```ini
[program:chatgpt-bridge]
user=chatgpt-bridge
directory=/opt/chatgpt-bridge
command=/opt/chatgpt-bridge/venv/bin/python -m uvicorn bridge.main:app --host 127.0.0.1 --port 8000
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/opt/chatgpt-bridge/logs/supervisor.log
environment=PATH="/opt/chatgpt-bridge/venv/bin",PYTHONUNBUFFERED="1"
```

Start supervisor:

```bash
sudo systemctl restart supervisor
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start chatgpt-bridge
```

#### 4. Configure Nginx

Create `/etc/nginx/sites-available/chatgpt-bridge`:

```nginx
upstream chatgpt_bridge {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name api.chatgpt-bridge.example.com;

    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.chatgpt-bridge.example.com;

    ssl_certificate /etc/letsencrypt/live/api.chatgpt-bridge.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.chatgpt-bridge.example.com/privkey.pem;

    client_max_body_size 10M;

    # WebSocket support
    proxy_read_timeout 86400;

    location / {
        proxy_pass http://chatgpt_bridge;
        proxy_http_version 1.1;

        # WebSocket headers
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Proxy headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /metrics {
        proxy_pass http://chatgpt_bridge;
        access_log off;
    }
}
```

Enable and restart:

```bash
sudo ln -s /etc/nginx/sites-available/chatgpt-bridge /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

### Production Considerations

#### High Availability Setup

1. **Redis Sentinel**: Use Redis Sentinel for automatic failover
   ```bash
   redis-sentinel /etc/redis/sentinel.conf
   ```

2. **Multiple Bridge Instances**: Run multiple instances behind load balancer
   ```bash
   # Instance 1
   API_PORT=8001 python -m uvicorn bridge.main:app

   # Instance 2
   API_PORT=8002 python -m uvicorn bridge.main:app

   # Instance 3
   API_PORT=8003 python -m uvicorn bridge.main:app
   ```

3. **Load Balancer Configuration** (Nginx):
   ```nginx
   upstream chatgpt_bridge {
       least_conn;
       server 127.0.0.1:8001;
       server 127.0.0.1:8002;
       server 127.0.0.1:8003;
   }
   ```

#### Session Persistence

Store session data in Redis for recovery after restart:

```python
# In session_pool.py
async def _load_sessions_from_redis(self):
    """Restore sessions from Redis on startup."""
    sessions = await self.redis.hgetall("sessions:active")
    for session_id, session_data in sessions.items():
        # Restore session...
```

#### Monitoring and Alerting

Set up Prometheus alerts:

```yaml
# prometheus-alerts.yml
groups:
  - name: chatgpt-bridge
    rules:
      - alert: HighErrorRate
        expr: rate(bridge_errors_total[5m]) > 0.05
        for: 5m
        annotations:
          summary: "High error rate detected"

      - alert: QueueBacklog
        expr: bridge_queue_depth > 100
        for: 10m
        annotations:
          summary: "Queue backlog accumulating"

      - alert: SessionExhaustion
        expr: bridge_sessions_active == bridge_sessions_max
        for: 5m
        annotations:
          summary: "All sessions in use"
```

---

## Debugging Guide

### Common Issues and Solutions

#### "Cannot locate chat input field" Error

**Problem**: The selector system cannot find the ChatGPT chat input box.

**Cause**: ChatGPT UI has changed, or selectors need rediscovery.

**Solution**:
1. Check if ChatGPT UI has updated
2. Trigger selector rediscovery:
   ```python
   import asyncio
   from bridge.selector_system import SelectorDiscovery, SelectorStore

   async def rediscover():
       discovery = SelectorDiscovery()
       store = SelectorStore("selectors.json")
       await store.load()
       results = await discovery.rediscover_all(page, store)
       await store.save()

   asyncio.run(rediscover())
   ```
3. Check browser screenshot: `/sessions/{id}/screenshot`
4. Manually verify selectors in browser dev tools (F12)

#### "No sessions available" Error

**Problem**: All browser sessions are in use and none are available.

**Cause**: Session pool exhausted, requests backing up in queue.

**Solution**:
1. Increase `SESSION_POOL_MAX` in .env:
   ```bash
   SESSION_POOL_MAX=10
   ```
2. Check for stuck sessions:
   ```bash
   curl http://localhost:8000/sessions
   ```
3. Restart bridge service to clear stuck sessions
4. Monitor queue depth: `curl http://localhost:8000/metrics`

#### "Redis connection refused" Error

**Problem**: Cannot connect to Redis server.

**Cause**: Redis not running or wrong connection URL.

**Solution**:
1. Check Redis is running:
   ```bash
   redis-cli ping
   # Should output: PONG
   ```
2. Verify Redis URL in .env matches actual Redis instance
3. Check firewall allows access to Redis port (default 6379)
4. Start Redis if not running:
   ```bash
   redis-server
   # Or with Docker:
   docker run -d -p 6379:6379 redis:7-alpine
   ```

#### "Login required" / Session Expired Error

**Problem**: ChatGPT session expires or login session lost.

**Cause**: Session cookies expired, IP changed, or suspicious activity detected.

**Solution**:
1. Set `BROWSER_HEADLESS=false` in .env to see browser
2. Run server and manually log in:
   ```bash
   BROWSER_HEADLESS=false bash scripts/start.sh
   ```
3. Browser window will open, allowing you to login
4. Cookies are persisted, subsequent requests will use saved session
5. Set `BROWSER_HEADLESS=true` after login
6. Restart server

#### "Response timeout" / Generation stuck

**Problem**: ChatGPT stops responding or takes too long.

**Cause**: Network issue, ChatGPT API problem, or timeout too short.

**Solution**:
1. Increase timeout in .env:
   ```bash
   RESPONSE_WAIT_TIMEOUT_S=300  # 5 minutes
   ```
2. Check ChatGPT status: `https://status.openai.com/`
3. Verify internet connection is stable
4. Check for rate limiting in logs
5. Recovery manager will auto-create new session if needed

#### "TypeError: Cannot read property 'value'" (React State Error)

**Problem**: React state inspection fails.

**Cause**: React Fiber tree structure changed or component not mounted.

**Solution**:
1. Fall back to DOM scraping (automatic via ui_fallback)
2. Check React DevTools: Open DevTools (F12), Profiler tab
3. Verify element is rendered before reading state
4. Add more robust selectors in selector_system.py

---

### Debug Endpoints

#### Screenshot Endpoint
```bash
# Capture current browser state
curl http://localhost:8000/sessions/{session_id}/screenshot > screenshot.png
```

#### Dashboard Endpoint
```bash
# Open in browser for live monitoring
open http://localhost:8000/dashboard
```

#### API Documentation
```bash
# Interactive Swagger UI
open http://localhost:8000/docs
```

#### Metrics Endpoint
```bash
# Get performance metrics
curl http://localhost:8000/metrics | jq .
```

---

### Enabling Debug Logs

Set `LOG_LEVEL=DEBUG` in `.env`:

```bash
LOG_LEVEL=DEBUG
DEBUG_MODE=true
BROWSER_SLOW_MO_MS=500  # Add 500ms delay to each action
```

Then restart:

```bash
bash scripts/start.sh
```

Debug output will be written to:
- Console (stdout/stderr)
- Log file: `logs/bridge.log`

Example debug output:

```
[DEBUG] Acquiring session from pool...
[DEBUG] Session acquired: sess_abc123
[DEBUG] Loading page: https://chat.openai.com/chat
[DEBUG] Waiting for chat input element...
[DEBUG] Found selector: textarea.rounded-lg
[DEBUG] Typing prompt: "What is 2+2?"
[DEBUG] Clicking send button...
[DEBUG] Waiting for response...
[DEBUG] Response received: "2+2 equals 4"
```

---

## Running Tests

### Prerequisites

```bash
# Install test dependencies
pip install pytest pytest-asyncio pytest-mock fakeredis
```

### Run All Tests

```bash
pytest tests/ -v
```

Output:
```
tests/test_queue.py::test_enqueue_and_dequeue_normal_priority PASSED
tests/test_queue.py::test_priority_ordering PASSED
tests/test_api.py::test_root_endpoint_returns_200 PASSED
...
```

### Run Specific Test File

```bash
pytest tests/test_queue.py -v
```

### Run with Coverage

```bash
pip install pytest-cov
pytest tests/ --cov=bridge --cov-report=html
open htmlcov/index.html
```

### Run with Detailed Output

```bash
pytest tests/ -vv --tb=long
```

### Run Single Test

```bash
pytest tests/test_queue.py::test_enqueue_and_dequeue_normal_priority -v
```

### Continuous Testing

```bash
pip install pytest-watch
ptw tests/
```

---

## Architecture Decisions

### Why Playwright over Selenium

- **Modern**: Built for modern single-page applications with async/await support
- **Performance**: Faster execution with better browser protocol implementation
- **Features**: Built-in network interception, better element waiting, CDP access
- **Maintenance**: More active development and community support
- **Async-first**: Native async/await support for concurrent session management

### Why React Fiber Inspection over DOM Scraping

- **Reliability**: Access data directly from component state, not derived from DOM
- **Performance**: Avoid parsing DOM repeatedly; state is already computed
- **Robustness**: Handles UI changes without selector updates
- **Accuracy**: Get exact data structure without serialization loss
- **Fallback**: Falls back to DOM scraping if Fiber inspection fails

### Why Redis over In-Memory Queue

- **Persistence**: Requests survive process restart
- **Distribution**: Multiple bridge instances can share the same queue
- **Scalability**: Supports millions of queued items without memory limits
- **Monitoring**: Built-in tools for queue inspection and debugging
- **Production-ready**: Battle-tested for critical systems

### Why SSE+WebSocket Dual Streaming

- **Simplicity**: SSE for simple server-to-client streaming (79% of use cases)
- **Low-latency**: WebSocket for bidirectional and lower-latency requirements
- **Compatibility**: SSE works everywhere, WebSocket for advanced clients
- **Fallback**: SSE-only clients can still use the API
- **Load balanced**: Both can work behind nginx/CDN proxies

---

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make changes and add tests
4. Run tests: `pytest tests/ -v`
5. Commit changes (`git commit -m 'Add amazing feature'`)
6. Push to branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## Support

For issues, questions, or contributions:

- **Issues**: https://github.com/yourusername/chatgpt-dom-bridge/issues
- **Discussions**: https://github.com/yourusername/chatgpt-dom-bridge/discussions
- **Email**: support@example.com

---

## Acknowledgments

- Built with [Playwright](https://playwright.dev/) for reliable browser automation
- API framework: [FastAPI](https://fastapi.tiangolo.com/)
- Queue system: [Redis](https://redis.io/)
- Testing: [Pytest](https://pytest.org/)

---

**Last Updated**: 2024-01-15
**Version**: 1.0.0
**Status**: Production Ready
# dom-api
