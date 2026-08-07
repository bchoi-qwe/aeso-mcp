# SPDX-License-Identifier: MIT
"""Typed configuration for AESO MCP."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aeso_mcp.errors import ConfigurationError

MarketTimezoneName = Literal["America/Edmonton"]


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="",
        populate_by_name=True,
    )

    aeso_api_key: SecretStr = Field(
        ...,
        description="AESO APIM subscription key from https://developer-apim.aeso.ca/",
        validation_alias=AliasChoices("AESO_API_KEY", "aeso_api_key"),
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("AESO_MCP_LOG_LEVEL", "log_level"),
    )
    http_connect_timeout_s: float = Field(
        default=10.0,
        validation_alias="AESO_MCP_HTTP_CONNECT_TIMEOUT_S",
        ge=1.0,
        le=60.0,
    )
    http_read_timeout_s: float = Field(
        default=60.0,
        validation_alias="AESO_MCP_HTTP_READ_TIMEOUT_S",
        ge=5.0,
        le=300.0,
    )
    http_max_retries: int = Field(
        default=3,
        validation_alias="AESO_MCP_HTTP_MAX_RETRIES",
        ge=0,
        le=8,
    )
    market_timezone: MarketTimezoneName = Field(
        default="America/Edmonton",
        validation_alias="AESO_MCP_MARKET_TIMEZONE",
    )
    # Query safety bounds
    max_pool_price_days: int = Field(default=366, ge=1, le=366)
    max_smp_days: int = Field(default=7, ge=1, le=31)
    max_load_days: int = Field(default=90, ge=1, le=366)
    max_smp_observations: int = Field(default=20_000, ge=1, le=100_000)
    max_price_observations: int = Field(default=10_000, ge=1, le=100_000)
    # Cache TTLs (seconds)
    cache_ttl_snapshot_s: float = Field(default=30.0, ge=0.0)
    cache_ttl_historical_s: float = Field(default=86_400.0, ge=0.0)
    cache_ttl_assets_s: float = Field(default=86_400.0, ge=0.0)
    cache_ttl_forecast_s: float = Field(default=300.0, ge=0.0)
    cache_max_entries: int = Field(
        default=512,
        ge=16,
        le=10_000,
        validation_alias="AESO_MCP_CACHE_MAX_ENTRIES",
        description="Maximum in-memory cache entries before eviction.",
    )

    aeso_base_url: str = Field(
        default="https://apimgw.aeso.ca/public",
        validation_alias="AESO_MCP_BASE_URL",
    )

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed:
            raise ValueError(f"AESO_MCP_LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized

    @property
    def api_key_value(self) -> str:
        """Return the raw API key string (never log this)."""
        return self.aeso_api_key.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings. Raises ConfigurationError on missing credentials."""
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:
        message = str(exc)
        if "AESO_API_KEY" in message or "aeso_api_key" in message:
            raise ConfigurationError(
                "AESO_API_KEY is required. Register at "
                "https://developer-apim.aeso.ca/ and set AESO_API_KEY in the environment "
                "or a local .env file. See .env.example."
            ) from None
        raise ConfigurationError(f"Invalid configuration: {exc}") from None


def clear_settings_cache() -> None:
    """Reset cached settings (tests only)."""
    get_settings.cache_clear()
