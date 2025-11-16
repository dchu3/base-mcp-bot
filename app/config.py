"""Application configuration management."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional

from pydantic import AnyHttpUrl, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment or `.env`."""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    gemini_model: str = Field(
        default="gemini-1.5-flash-latest",
        alias="GEMINI_MODEL",
    )
    planner_prompt_file: Optional[Path] = Field(
        default=None,
        alias="PLANNER_PROMPT_FILE",
    )
    telegram_chat_id: Optional[int] = Field(default=None, alias="TELEGRAM_CHAT_ID")

    mcp_base_server_cmd: str = Field(
        default="node ../base-mcp-server/dist/index.js start",
        alias="MCP_BASE_SERVER_CMD",
    )
    mcp_dexscreener_cmd: str = Field(
        default="node mcp-servers/mcp-dexscreener/index.js",
        alias="MCP_DEXSCREENER_CMD",
    )
    mcp_honeypot_cmd: str = Field(
        default='bash -lc "cd ../base-mcp-honeypot && node dist/server.js stdio"',
        alias="MCP_HONEYPOT_CMD",
    )

    base_network: str = Field(default="base-mainnet", alias="BASE_NETWORK")
    routers_json: Optional[Path] = Field(
        default=None,
        alias="ROUTERS_JSON",
    )

    default_lookback_minutes: int = Field(
        default=30,
        alias="DEFAULT_LOOKBACK_MINUTES",
        ge=1,
        le=120,
    )
    max_items: int = Field(default=20, alias="MAX_ITEMS", ge=1, le=100)
    rate_limit_per_user_per_min: int = Field(
        default=10,
        alias="RATE_LIMIT_PER_USER_PER_MIN",
        ge=1,
        le=60,
    )

    database_url: str = Field(
        default="sqlite+aiosqlite:///./.tmp/state.db",
        alias="DATABASE_URL",
    )

    scheduler_interval_minutes: int = Field(
        default=60,
        alias="SCHEDULER_INTERVAL_MINUTES",
        ge=1,
        le=60,
    )
    healthcheck_url: Optional[AnyHttpUrl] = Field(
        default=None,
        alias="HEALTHCHECK_URL",
    )

    admin_user_ids: List[int] = Field(default_factory=list, alias="ADMIN_USER_IDS")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    planner_confidence_threshold: float = Field(
        default=0.7,
        alias="PLANNER_CONFIDENCE_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    planner_enable_reflection: bool = Field(
        default=True, alias="PLANNER_ENABLE_REFLECTION"
    )
    planner_max_iterations: int = Field(
        default=2, alias="PLANNER_MAX_ITERATIONS", ge=1, le=5
    )

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: Any) -> List[int]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple, set)):
            return [int(v) for v in value]
        return [int(value)]


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Return cached Settings instance, raising a helpful message on failure."""
    try:
        return Settings()
    except (
        ValidationError
    ) as exc:  # pragma: no cover - configuration failure visible on boot
        raise RuntimeError(f"Invalid configuration: {exc}") from exc


__all__ = ["Settings", "load_settings"]
