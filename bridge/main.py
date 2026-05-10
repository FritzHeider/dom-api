"""
FastAPI application entry point for ChatGPT DOM Agent Bridge.
Manages application lifecycle, component initialization, and API routing.
"""

import asyncio
import time
import signal
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware

try:
    from prometheus_fastapi_instrumentator import Instrumentator
except ImportError:
    Instrumentator = None

from bridge.config import settings
from bridge.logging_setup import configure_logging, get_logger
from bridge.browser_controller.controller import BrowserController
from bridge.queue.manager import QueueManager
from bridge.session_pool.pool import SessionPool
from bridge.recovery.recovery import RecoveryManager
from bridge.api.routes import router
from bridge.monitoring.metrics import MetricsCollector
from bridge.monitoring.dashboard import get_dashboard_html

logger = get_logger("main")


# ── Application Lifespan ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.
    Handles startup and shutdown of all application components.
    """
    # ── STARTUP ────────────────────────────────────────────────────────────────
    configure_logging(settings.log_level, settings.log_format)
    logger.info(
        "ChatGPT DOM Agent Bridge starting up",
        version="1.0.0",
        host=settings.api_host,
        port=settings.api_port,
    )

    try:
        # Initialize QueueManager for request queueing
        logger.info("Initializing QueueManager", redis_url=settings.redis_url)
        queue_manager = QueueManager()
        await queue_manager.connect()

        # Initialize BrowserController for Playwright management
        logger.info(
            "Initializing BrowserController",
            headless=True,
        )
        browser_controller = BrowserController(settings)
        await browser_controller.start()

        # Initialize SessionPool for session management
        logger.info(
            "Initializing SessionPool",
            min_sessions=settings.session_pool_min,
            max_sessions=settings.session_pool_max,
        )
        session_pool = SessionPool(browser_controller)
        await session_pool.start()

        # Initialize RecoveryManager for fault tolerance
        logger.info("Initializing RecoveryManager")
        recovery_manager = RecoveryManager(session_pool, queue_manager)

        # Store all components in app state for dependency injection
        app.state.queue_manager = queue_manager
        app.state.browser_controller = browser_controller
        app.state.session_pool = session_pool
        app.state.recovery_manager = recovery_manager
        app.state.start_time = time.monotonic()

        # Initialize metrics store
        app.state.metrics_store = {
            "total_requests": 0,
            "successful_requests": 0,
            "latencies": [],
        }

        # Start background health check task
        health_task = asyncio.create_task(_health_check_loop(app))
        app.state.health_task = health_task

        logger.info(
            "Bridge startup complete",
            components_initialized=5,
            queue_manager="ready",
            browser_controller="ready",
            session_pool="ready",
            recovery_manager="ready",
        )

    except Exception as e:
        logger.error("Bridge startup failed", error=str(e))
        raise

    yield  # ← Application is now running

    # ── SHUTDOWN ───────────────────────────────────────────────────────────────
    logger.info("Bridge shutting down")

    try:
        # Cancel health check
        if hasattr(app.state, "health_task"):
            app.state.health_task.cancel()
            try:
                await app.state.health_task
            except asyncio.CancelledError:
                pass

        # Shutdown session pool
        logger.info("Shutting down SessionPool")
        await session_pool.stop()

        # Shutdown browser controller
        logger.info("Shutting down BrowserController")
        await browser_controller.stop()

        # Shutdown queue manager
        logger.info("Disconnecting QueueManager")
        await queue_manager.disconnect()

        logger.info("Bridge shutdown complete")

    except Exception as e:
        logger.error("Error during shutdown", error=str(e))


async def _health_check_loop(app: FastAPI) -> None:
    """
    Background periodic health check task.
    Runs every 30 seconds to monitor and recover sessions.
    """
    while True:
        try:
            await asyncio.sleep(30)
            logger.debug("Running periodic health check")
            await app.state.recovery_manager.check_all_sessions()
        except asyncio.CancelledError:
            logger.debug("Health check loop cancelled")
            break
        except Exception as e:
            logger.error("Health check error", error=str(e))


# ── FastAPI App Factory ────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    app = FastAPI(
        title="ChatGPT DOM Agent Bridge",
        description="Programmatic access to ChatGPT via browser automation and DOM scraping",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── CORS Middleware ────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["*"],
    )

    # ── Prometheus Instrumentation ────────────────────────────────────────────
    if Instrumentator:
        try:
            Instrumentator().instrument(app).expose(
                app,
                endpoint="/prometheus",
            )
            logger.info("Prometheus instrumentation enabled")
        except Exception as e:
            logger.warning("Prometheus instrumentation failed", error=str(e))

    # ── API Routes ─────────────────────────────────────────────────────────────
    app.include_router(router, prefix="")

    # ── Dashboard Endpoint ─────────────────────────────────────────────────────
    @app.get("/dashboard", response_class=HTMLResponse, tags=["System"])
    async def get_dashboard() -> str:
        """
        Return the real-time monitoring dashboard.
        Auto-refreshes every 5 seconds.
        """
        return get_dashboard_html()

    # ── Raw Prometheus Metrics ────────────────────────────────────────────────
    @app.get("/prometheus_metrics", tags=["System"])
    async def get_prometheus_metrics() -> Response:
        """
        Return raw Prometheus-format metrics.
        Use /metrics for human-readable metrics.
        """
        data, content_type = MetricsCollector.get_prometheus_output()
        return Response(content=data, media_type=content_type)

    # ── Health Check ───────────────────────────────────────────────────────────
    @app.get("/health", tags=["System"])
    async def health_check() -> dict:
        """
        Simple health check endpoint.
        Returns 200 if service is up.
        """
        return {
            "status": "ok",
            "version": "1.0.0",
            "timestamp": time.time(),
        }

    # ── Root Redirect ──────────────────────────────────────────────────────────
    @app.get("/", tags=["System"])
    async def root() -> dict:
        """Root endpoint with links to documentation and dashboard."""
        return {
            "message": "ChatGPT DOM Agent Bridge",
            "docs": "/docs",
            "dashboard": "/dashboard",
            "health": "/health",
            "metrics": "/metrics",
            "prometheus": "/prometheus",
        }

    return app


# ── Create App Instance ────────────────────────────────────────────────────────

app = create_app()


# ── Entry Point ────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    logger.info(
        "Starting Uvicorn server",
        host=settings.api_host,
        port=settings.api_port,
        debug=settings.api_debug,
    )

    uvicorn.run(
        "bridge.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_debug,
        log_level=settings.log_level.lower(),
        access_log=True,
        workers=1,
    )
