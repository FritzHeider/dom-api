from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    CollectorRegistry,
    REGISTRY,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from bridge.logging_setup import get_logger

logger = get_logger("metrics")


# ── Metric Definitions ─────────────────────────────────────────────────────────

REQUESTS_TOTAL = Counter(
    "bridge_requests_total",
    "Total number of chat requests",
    ["status", "source", "priority"],
    registry=REGISTRY,
)

REQUEST_LATENCY = Histogram(
    "bridge_request_latency_seconds",
    "Request latency in seconds",
    ["source"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
    registry=REGISTRY,
)

ACTIVE_SESSIONS = Gauge(
    "bridge_active_sessions",
    "Number of active browser sessions",
    ["status"],
    registry=REGISTRY,
)

QUEUE_LENGTH = Gauge(
    "bridge_queue_length",
    "Number of requests in queue",
    ["priority"],
    registry=REGISTRY,
)

TOKEN_STREAM_RATE = Counter(
    "bridge_tokens_streamed_total",
    "Total tokens streamed to clients",
    registry=REGISTRY,
)

RECOVERY_EVENTS = Counter(
    "bridge_recovery_events_total",
    "Total recovery events triggered",
    ["reason", "action"],
    registry=REGISTRY,
)

SELECTOR_FAILURES = Counter(
    "bridge_selector_failures_total",
    "Total selector discovery failures",
    ["selector_type"],
    registry=REGISTRY,
)

ERROR_TYPES = Counter(
    "bridge_errors_total",
    "Total errors by type",
    ["error_type"],
    registry=REGISTRY,
)

RESPONSE_TIME_PERCENTILE = Histogram(
    "bridge_response_time_percentile",
    "Response time distribution for percentile calculations",
    ["endpoint"],
    registry=REGISTRY,
)


class MetricsCollector:
    """
    Utility class for recording and retrieving Prometheus metrics.
    Provides high-level methods for metric recording.
    """

    @staticmethod
    def record_request(
        status: str,
        source: str,
        priority: str,
        latency_s: float,
    ) -> None:
        """
        Record a completed request.

        Args:
            status: "success" or "failure"
            source: "network_stream", "react_state", "dom_fallback", etc.
            priority: "low", "normal", "high"
            latency_s: request latency in seconds
        """
        REQUESTS_TOTAL.labels(
            status=status,
            source=source,
            priority=priority,
        ).inc()
        REQUEST_LATENCY.labels(source=source).observe(latency_s)
        logger.debug(
            "Request recorded",
            status=status,
            source=source,
            priority=priority,
            latency_s=latency_s,
        )

    @staticmethod
    def record_session_status(status: str, count: int) -> None:
        """
        Record active session count by status.

        Args:
            status: "idle", "busy", "error"
            count: number of sessions in this status
        """
        ACTIVE_SESSIONS.labels(status=status).set(count)
        logger.debug(
            "Session status recorded",
            status=status,
            count=count,
        )

    @staticmethod
    def record_queue_length(priority: str, count: int) -> None:
        """
        Record queue length by priority.

        Args:
            priority: "low", "normal", "high"
            count: number of requests in queue
        """
        QUEUE_LENGTH.labels(priority=priority).set(count)

    @staticmethod
    def record_tokens(count: int = 1) -> None:
        """
        Record tokens streamed to clients.

        Args:
            count: number of tokens (default: 1)
        """
        TOKEN_STREAM_RATE.inc(count)

    @staticmethod
    def record_recovery(reason: str, action: str) -> None:
        """
        Record a recovery event.

        Args:
            reason: "session_crash", "timeout", "network_error", etc.
            action: "restart_session", "redirect_to_queue", "manual_recovery", etc.
        """
        RECOVERY_EVENTS.labels(reason=reason, action=action).inc()
        logger.info(
            "Recovery event recorded",
            reason=reason,
            action=action,
        )

    @staticmethod
    def record_selector_failure(selector_type: str) -> None:
        """
        Record a selector discovery failure.

        Args:
            selector_type: "input_box", "send_button", "response_area", etc.
        """
        SELECTOR_FAILURES.labels(selector_type=selector_type).inc()
        logger.warning(
            "Selector failure recorded",
            selector_type=selector_type,
        )

    @staticmethod
    def record_error(error_type: str) -> None:
        """
        Record an error by type.

        Args:
            error_type: "timeout", "browser_crash", "auth_failure", etc.
        """
        ERROR_TYPES.labels(error_type=error_type).inc()
        logger.warning(
            "Error recorded",
            error_type=error_type,
        )

    @staticmethod
    def record_response_time(endpoint: str, latency_s: float) -> None:
        """
        Record response time for percentile analysis.

        Args:
            endpoint: "/chat", "/new_chat", "/status", etc.
            latency_s: response latency in seconds
        """
        RESPONSE_TIME_PERCENTILE.labels(endpoint=endpoint).observe(latency_s)

    @staticmethod
    def get_prometheus_output() -> tuple[bytes, str]:
        """
        Generate Prometheus-format metrics output.

        Returns:
            Tuple of (metrics bytes, content type string)
        """
        return generate_latest(REGISTRY), CONTENT_TYPE_LATEST

    @staticmethod
    def get_metrics_dict() -> dict:
        """
        Get current metrics as a dictionary.
        Useful for in-memory access without Prometheus format.

        Returns:
            Dictionary with metric names and values
        """
        try:
            from prometheus_client import CollectorRegistry, generate_latest
            import re

            output = generate_latest(REGISTRY).decode("utf-8")
            metrics = {}

            # Parse Prometheus text format
            for line in output.split("\n"):
                if line.startswith("#") or not line.strip():
                    continue

                # Extract metric name and value
                match = re.match(r"(\w+(?:{[^}]*})?) ([\d.e+-]+)", line)
                if match:
                    name, value = match.groups()
                    try:
                        metrics[name] = float(value)
                    except ValueError:
                        pass

            return metrics
        except Exception as e:
            logger.error("Failed to extract metrics dict", error=str(e))
            return {}
