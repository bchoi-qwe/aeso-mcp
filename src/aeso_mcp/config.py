# SPDX-License-Identifier: MIT
"""Typed configuration for AESO MCP."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from aeso_mcp.errors import ConfigurationError

MarketTimezoneName = Literal["America/Edmonton"]
HttpStringList = Annotated[list[str] | None, NoDecode]


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
    # HTTP runtime controls. These settings affect HTTP transport only; stdio
    # does not construct or apply the HTTP runtime middleware.
    http_bearer_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AESO_MCP_HTTP_BEARER_TOKEN",
            "AESO_MCP_BEARER_TOKEN",
            "http_bearer_token",
        ),
        description="Optional bearer token required by the remote HTTP transport.",
    )
    http_allowed_origins: HttpStringList = Field(
        default=None,
        validation_alias=AliasChoices(
            "AESO_MCP_HTTP_ALLOWED_ORIGINS",
            "http_allowed_origins",
        ),
        description="Explicit browser origins allowed to call the HTTP transport.",
    )
    http_allowed_hosts: HttpStringList = Field(
        default=None,
        validation_alias=AliasChoices(
            "AESO_MCP_HTTP_ALLOWED_HOSTS",
            "http_allowed_hosts",
        ),
        description="Explicit Host header values accepted by the HTTP transport.",
    )
    http_rate_limit_requests: int = Field(
        default=120,
        validation_alias=AliasChoices(
            "AESO_MCP_HTTP_RATE_LIMIT_REQUESTS",
            "http_rate_limit_requests",
        ),
        ge=1,
        le=100_000,
        description="Maximum HTTP requests per client within the rate-limit window.",
    )
    http_rate_limit_window_s: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "AESO_MCP_HTTP_RATE_LIMIT_WINDOW_S",
            "http_rate_limit_window_s",
        ),
        gt=0.0,
        le=86_400.0,
        description="Per-client HTTP rate-limit window in seconds.",
    )
    http_max_concurrent_requests: int = Field(
        default=32,
        validation_alias=AliasChoices(
            "AESO_MCP_HTTP_MAX_CONCURRENT_REQUESTS",
            "http_max_concurrent_requests",
        ),
        ge=1,
        le=10_000,
        description="Maximum concurrent HTTP requests admitted to MCP tools.",
    )
    http_max_request_body_bytes: int = Field(
        default=1_048_576,
        validation_alias=AliasChoices(
            "AESO_MCP_HTTP_MAX_REQUEST_BODY_BYTES",
            "AESO_MCP_HTTP_BODY_MAX_BYTES",
            "http_max_request_body_bytes",
        ),
        ge=1,
        le=16_777_216,
        description="Maximum inbound HTTP request body size in bytes.",
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
    cache_ttl_public_report_s: float = Field(
        default=900.0,
        ge=0.0,
        validation_alias="AESO_MCP_CACHE_TTL_PUBLIC_REPORT_S",
        description="TTL for current approved public-report publications.",
    )
    cache_ttl_historical_public_report_s: float = Field(
        default=86_400.0,
        ge=0.0,
        validation_alias="AESO_MCP_CACHE_TTL_HISTORICAL_PUBLIC_REPORT_S",
        description=(
            "TTL for historical approved transmission publications (immutable archive snapshots)."
        ),
    )
    cache_ttl_long_range_outages_s: float = Field(
        default=1_800.0,
        ge=0.0,
        validation_alias="AESO_MCP_CACHE_TTL_LONG_RANGE_OUTAGES_S",
        description="TTL for long-range significant transmission outage publications.",
    )
    cache_ttl_market_power_s: float = Field(
        default=300.0,
        ge=0.0,
        validation_alias="AESO_MCP_CACHE_TTL_MARKET_POWER_S",
        description="TTL for MCSINR / Secondary Offer Price Limit current publications.",
    )
    cache_max_entries: int = Field(
        default=512,
        ge=16,
        le=10_000,
        validation_alias="AESO_MCP_CACHE_MAX_ENTRIES",
        description="Maximum in-memory cache entries before eviction.",
    )
    max_transmission_outage_history_days: int = Field(
        default=14,
        ge=1,
        le=31,
        validation_alias="AESO_MCP_MAX_TX_OUTAGE_HISTORY_DAYS",
        description=(
            "Max historical window for approved transmission outage publication navigation."
        ),
    )

    aeso_base_url: str = Field(
        default="https://apimgw.aeso.ca/public",
        validation_alias="AESO_MCP_BASE_URL",
    )

    @field_validator("aeso_base_url")
    @classmethod
    def _validate_aeso_base_url(cls, value: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("AESO_MCP_BASE_URL must be an absolute HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Credentials must not appear in AESO_MCP_BASE_URL")
        if parsed.query or parsed.fragment:
            raise ValueError("AESO_MCP_BASE_URL must not include a query string or fragment")
        host = parsed.hostname.lower()
        if host != "apimgw.aeso.ca":
            raise ValueError(
                "AESO_MCP_BASE_URL host must be apimgw.aeso.ca (authenticated APIM gateway only)."
            )
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed:
            raise ValueError(f"AESO_MCP_LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized

    @field_validator("http_allowed_origins", "http_allowed_hosts", mode="before")
    @classmethod
    def _parse_http_string_list(cls, value: Any) -> Any:
        """Accept either JSON lists or comma-separated environment values."""
        if value is None or isinstance(value, (list, tuple)):
            return list(value) if isinstance(value, tuple) else value
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if not isinstance(parsed, list):
                raise ValueError("HTTP allow-list values must be a list")
            return parsed
        return [item.strip() for item in stripped.split(",") if item.strip()]

    @field_validator("http_allowed_origins")
    @classmethod
    def _validate_http_allowed_origins(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        from urllib.parse import urlsplit

        normalized: list[str] = []
        for origin in value:
            candidate = origin.strip().rstrip("/")
            parsed = urlsplit(candidate)
            if (
                any(character in candidate for character in "*?")
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "AESO_MCP_HTTP_ALLOWED_ORIGINS must contain explicit http(s) origins"
                )
            # Force port parsing now so malformed values fail at startup.
            try:
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("AESO_MCP_HTTP_ALLOWED_ORIGINS contains an invalid port") from exc
            normalized.append(candidate)
        return normalized or None

    @field_validator("http_allowed_hosts")
    @classmethod
    def _validate_http_allowed_hosts(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        from urllib.parse import urlsplit

        normalized: list[str] = []
        for host in value:
            candidate = host.strip()
            parsed = urlsplit(f"//{candidate}")
            if (
                not candidate
                or any(character in candidate for character in "*?")
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("AESO_MCP_HTTP_ALLOWED_HOSTS must contain explicit host values")
            try:
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("AESO_MCP_HTTP_ALLOWED_HOSTS contains an invalid port") from exc
            normalized.append(candidate)
        return normalized or None

    @field_validator("http_bearer_token")
    @classmethod
    def _validate_http_bearer_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        token = value.get_secret_value()
        if not token or any(character.isspace() for character in token):
            raise ValueError("AESO_MCP_HTTP_BEARER_TOKEN must be a non-empty token")
        return value

    @property
    def api_key_value(self) -> str:
        """Return the raw API key string (never log this)."""
        return self.aeso_api_key.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings. Raises ConfigurationError on missing credentials."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        missing_api_key = any(
            error.get("type") == "missing"
            and any(str(part).lower() == "aeso_api_key" for part in error.get("loc", ()))
            for error in errors
        )
        if missing_api_key:
            raise ConfigurationError(
                "AESO_API_KEY is required. Register at "
                "https://developer-apim.aeso.ca/ and set AESO_API_KEY in the environment "
                "or a local .env file. See .env.example."
            ) from None
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', ())) or 'settings'}: "
            f"{error.get('msg', 'invalid value')}"
            for error in errors
        )
        raise ConfigurationError(f"Invalid configuration: {details}") from None
    except Exception as exc:
        raise ConfigurationError(f"Unable to load configuration ({type(exc).__name__}).") from None


def clear_settings_cache() -> None:
    """Reset cached settings (tests only)."""
    get_settings.cache_clear()
