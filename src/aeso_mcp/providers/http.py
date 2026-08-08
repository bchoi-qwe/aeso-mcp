# SPDX-License-Identifier: MIT
"""HTTP client for direct AESO APIM access."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from aeso_mcp.config import Settings
from aeso_mcp.errors import (
    AuthenticationError,
    DataValidationError,
    RateLimitError,
    UpstreamUnavailableError,
)

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({"apimgw.aeso.ca"})


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RateLimitError | UpstreamUnavailableError | httpx.TransportError)


def _before_sleep(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "aeso_http_retry attempt=%s error=%s",
        retry_state.attempt_number,
        type(exc).__name__ if exc else "unknown",
    )


class AesoHttpClient:
    """Authenticated httpx client bound to the AESO APIM gateway."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.aeso_base_url.rstrip("/"),
            headers={
                "Cache-Control": "no-cache",
                "API-KEY": settings.api_key_value,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout_s,
                read=settings.http_read_timeout_s,
                write=settings.http_read_timeout_s,
                pool=settings.http_connect_timeout_s,
            ),
        )

    async def aclose(self) -> None:
        if self._client.is_closed:
            return
        await self._client.aclose()

    async def get_json(self, endpoint: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET a relative AESO endpoint and return parsed JSON."""
        path = endpoint.lstrip("/")
        self._assert_allowed_url(path)

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.http_max_retries + 1),
            wait=wait_exponential_jitter(initial=0.5, max=8.0),
            retry=retry_if_exception(_is_retryable),
            before_sleep=_before_sleep,
            reraise=True,
        ):
            with attempt:
                return await self._get_once(path, params=params)
        raise UpstreamUnavailableError("AESO request failed after retries.")

    async def _get_once(self, path: str, *, params: dict[str, Any] | None) -> Any:
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise UpstreamUnavailableError("AESO API request timed out.") from exc
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError("Failed to connect to AESO API.") from exc

        status = response.status_code
        logger.info(
            "aeso_http_get path=%s status=%s duration_ms=%.1f",
            path.split("?", 1)[0],
            status,
            response.elapsed.total_seconds() * 1000,
        )

        if status in {401, 403}:
            raise AuthenticationError(
                "AESO API authentication failed. Check that AESO_API_KEY is valid "
                "and subscribed to the public API product."
            )
        if status == 404:
            raise DataValidationError(f"AESO endpoint not found: {path.split('?', 1)[0]}")
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            retry_s = float(retry_after) if retry_after and retry_after.isdigit() else None
            raise RateLimitError(retry_after_s=retry_s)
        if status >= 500:
            raise UpstreamUnavailableError(f"AESO API returned HTTP {status}.")
        if status >= 400:
            raise DataValidationError(f"AESO API rejected the request (HTTP {status}).")

        try:
            return response.json()
        except ValueError as exc:
            raise DataValidationError("AESO API returned malformed JSON.") from exc

    def _assert_allowed_url(self, path: str) -> None:
        # Relative paths only — base_url host is fixed. Reject absolute URLs.
        if path.startswith("http://") or path.startswith("https://"):
            raise DataValidationError("Absolute upstream URLs are not allowed.")
        host = httpx.URL(self._settings.aeso_base_url).host
        if host not in ALLOWED_HOSTS:
            raise DataValidationError(f"Upstream host not allow-listed: {host}")
