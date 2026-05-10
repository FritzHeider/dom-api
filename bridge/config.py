"""
Central configuration module for ChatGPT DOM Agent Bridge.
All settings are driven by environment variables with sensible defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BridgeConfig(BaseSettings):
    """Master configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── API Server ────────────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0", description="Bind host for FastAPI")
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_debug: bool = Field(default=False)
    api_secret_key: str = Field(default="change-me-in-production")

    # ── Browser ───────────────────────────────────────────────────────────────
    browser_headless: bool = Field(default=False)
    browser_profile_dir: Path = Field(default=Path("./browser_profiles"))
    chatgpt_url: str = Field(default="https://chatgpt.com")
    browser_timeout_ms: int = Field(default=30_000)
    browser_slow_mo_ms: int = Field(default=0)
    browser_viewport_width: int = Field(default=1280)
    browser_viewport_height: int = Field(default=900)

    # ── Session Pool ──────────────────────────────────────────────────────────
    session_pool_min: int = Field(default=1, ge=1)
    session_pool_max: int = Field(default=5, ge=1, le=10)
    session_idle_timeout_s: int = Field(default=300)

    # ── Redis Queue ───────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")
    queue_name: str = Field(default="chatgpt_bridge_queue")
    queue_max_retry: int = Field(default=3)
    queue_priority_levels: int = Field(default=3)

    # ── Recovery ──────────────────────────────────────────────────────────────
    recovery_max_retries: int = Field(default=3)
    recovery_backoff_base_s: float = Field(default=2.0)
    recovery_session_restart_delay_s: float = Field(default=5.0)

    # ── Monitoring ────────────────────────────────────────────────────────────
    metrics_port: int = Field(default=9090)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")

    # ── Timeouts ──────────────────────────────────────────────────────────────
    prompt_submit_timeout_s: int = Field(default=10)
    response_wait_timeout_s: int = Field(default=120)
    stream_idle_timeout_s: int = Field(default=30)

    @field_validator("session_pool_max")
    @classmethod
    def validate_pool_max(cls, v: int, info) -> int:
        min_val = info.data.get("session_pool_min", 1)
        if v < min_val:
            raise ValueError(
                f"session_pool_max ({v}) must be >= session_pool_min ({min_val})"
            )
        return v

    @field_validator("browser_profile_dir", mode="before")
    @classmethod
    def ensure_profile_dir(cls, v) -> Path:
        path = Path(v)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_profile_path(self, session_id: str) -> Path:
        """Return the persistent profile directory for a given session."""
        path = self.browser_profile_dir / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path


# Global singleton — import this everywhere
settings = BridgeConfig()
