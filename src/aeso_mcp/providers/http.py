# SPDX-License-Identifier: MIT
"""HTTP client for direct AESO APIM access."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse

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
_MAX_REDIRECTS = 5
_MAX_AUTOMATIC_RETRY_AFTER_S = 60.0
_FALLBACK_RETRY_WAIT = wait_exponential_jitter(initial=0.5, max=8.0)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError) and exc.retry_after_s is not None:
        # Do not retry before a long server-requested delay. Return the error and
        # its retry_after_s field so the caller can schedule the retry instead.
        return exc.retry_after_s <= _MAX_AUTOMATIC_RETRY_AFTER_S
    return isinstance(exc, RateLimitError | UpstreamUnavailableError | httpx.TransportError)


def _retry_wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, RateLimitError) and exc.retry_after_s is not None:
        return max(exc.retry_after_s, 0.0)
    return float(_FALLBACK_RETRY_WAIT(retry_state))


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
            # Manual redirects so API-KEY is never forwarded off apimgw.aeso.ca.
            # httpx only strips Authorization on cross-origin redirects, not API-KEY.
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        if self._client.is_closed:
            return
        await self._client.aclose()

    async def get_json(self, endpoint: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET a relative AESO endpoint and return parsed JSON."""
        path = endpoint.lstrip("/")
        self._assert_allowed_path(path)

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.http_max_retries + 1),
            wait=_retry_wait,
            retry=retry_if_exception(_is_retryable),
            before_sleep=_before_sleep,
            reraise=True,
        ):
            with attempt:
                return await self._get_once(path, params=params)
        raise UpstreamUnavailableError("AESO request failed after retries.")

    async def _get_once(self, path: str, *, params: dict[str, Any] | None) -> Any:
        current_path = path
        current_params = params
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                response = await self._client.get(current_path, params=current_params)
            except httpx.TimeoutException as exc:
                raise UpstreamUnavailableError("AESO API request timed out.") from exc
            except httpx.TransportError as exc:
                raise UpstreamUnavailableError("Failed to connect to AESO API.") from exc

            status = response.status_code
            logger.info(
                "aeso_http_get path=%s status=%s duration_ms=%.1f",
                current_path.split("?", 1)[0],
                status,
                response.elapsed.total_seconds() * 1000,
            )

            if status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise DataValidationError(
                        f"AESO API redirect missing Location (HTTP {status})."
                    )
                current_path, current_params = self._resolve_redirect(
                    response_url=str(response.url),
                    location=location,
                )
                continue

            if status in {401, 403}:
                raise AuthenticationError(
                    "AESO API authentication failed. Check that AESO_API_KEY is valid "
                    "and subscribed to the public API product."
                )
            if status == 404:
                raise DataValidationError(
                    f"AESO endpoint not found: {current_path.split('?', 1)[0]}"
                )
            if status == 429:
                retry_s = _parse_retry_after(response.headers.get("Retry-After"))
                raise RateLimitError(retry_after_s=retry_s)
            if status >= 500:
                raise UpstreamUnavailableError(f"AESO API returned HTTP {status}.")
            if status >= 400:
                raise DataValidationError(f"AESO API rejected the request (HTTP {status}).")

            try:
                return response.json()
            except ValueError as exc:
                raise DataValidationError("AESO API returned malformed JSON.") from exc

        raise DataValidationError(
            f"AESO API exceeded {_MAX_REDIRECTS} redirects while staying allow-listed."
        )

    def _resolve_redirect(self, *, response_url: str, location: str) -> tuple[str, None]:
        """Resolve a redirect Location to an allow-listed absolute APIM URL."""
        absolute = urljoin(response_url, location)
        parsed = urlparse(absolute)
        if parsed.scheme != "https":
            raise DataValidationError("AESO API redirect must use HTTPS.")
        if parsed.username is not None or parsed.password is not None:
            raise DataValidationError("Credentials must not appear in AESO API redirect URLs.")
        host = (parsed.hostname or "").lower()
        if host not in ALLOWED_HOSTS:
            raise DataValidationError(
                f"AESO API redirected off allow-listed host: {host or '(missing)'}"
            )
        # Absolute URL so base_url joining cannot alter host; API-KEY stays on allow-list only.
        return absolute, None

    def _assert_allowed_path(self, path: str) -> None:
        # Relative paths only — base_url host is fixed. Reject absolute URLs.
        if path.startswith("http://") or path.startswith("https://"):
            raise DataValidationError("Absolute upstream URLs are not allowed.")
        host = httpx.URL(self._settings.aeso_base_url).host
        if host not in ALLOWED_HOSTS:
            raise DataValidationError(f"Upstream host not allow-listed: {host}")


def _parse_retry_after(value: str | None) -> float | None:
    """Parse standard Retry-After delta-seconds or HTTP-date values."""
    if value is None:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max((retry_at - datetime.now(tz=UTC)).total_seconds(), 0.0)
    return max(seconds, 0.0)
