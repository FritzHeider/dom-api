"""
Structured logging setup using structlog + python-json-logger.
Provides contextvars-based request ID injection and flexible formatting.
"""

from __future__ import annotations

import contextvars
import logging
import logging.config
import sys
from datetime import datetime
from typing import Any, Optional

import structlog
from pythonjsonlogger import jsonlogger

# Context variable for request ID tracing
_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)
_session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "session_id", default=None
)


def set_request_id(request_id: str) -> None:
    """Set the current request ID in context."""
    _request_id_var.set(request_id)


def set_session_id(session_id: str) -> None:
    """Set the current session ID in context."""
    _session_id_var.set(session_id)


def clear_context() -> None:
    """Clear all context variables."""
    _request_id_var.set(None)
    _session_id_var.set(None)


class ContextFilter(logging.Filter):
    """Injects request_id and session_id from contextvars into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = _request_id_var.get()
        session_id = _session_id_var.get()

        if request_id:
            record.request_id = request_id
        else:
            record.request_id = "-"

        if session_id:
            record.session_id = session_id
        else:
            record.session_id = "-"

        return True


class TimeStampedJSONFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that adds ISO 8601 timestamp."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = datetime.utcnow().isoformat() + "Z"
        log_record["level"] = record.levelname
        log_record["module"] = record.name
        if hasattr(record, "request_id"):
            log_record["request_id"] = record.request_id
        if hasattr(record, "session_id"):
            log_record["session_id"] = record.session_id


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter with colors."""

    LEVEL_COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).isoformat()
        level = record.levelname
        color = self.LEVEL_COLORS.get(level, "")
        module = record.name

        request_id = getattr(record, "request_id", "-")
        session_id = getattr(record, "session_id", "-")

        context_parts = []
        if request_id != "-":
            context_parts.append(f"req={request_id}")
        if session_id != "-":
            context_parts.append(f"sess={session_id}")
        context_str = " [" + " ".join(context_parts) + "]" if context_parts else ""

        formatted = (
            f"{timestamp} {color}{level:8}{self.RESET} {module:30} {record.getMessage()}{context_str}"
        )
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        return formatted


def configure_logging(level: str = "INFO", format: str = "json") -> None:
    """
    Configure structured logging with structlog and Python logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        format: Log format (json or console)
    """
    # Configure Python's standard logging first
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": TimeStampedJSONFormatter},
            "console": {"()": ConsoleFormatter},
        },
        "filters": {
            "context": {"()": ContextFilter},
        },
        "handlers": {
            "default": {
                "level": level,
                "class": "logging.StreamHandler",
                "formatter": format,
                "filters": ["context"],
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "": {
                "handlers": ["default"],
                "level": level,
                "propagate": True,
            },
        },
    }

    logging.config.dictConfig(logging_config)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if format == "console"
            else structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a bound structlog logger.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Bound structlog logger with optional context injection
    """
    return structlog.get_logger(name)
