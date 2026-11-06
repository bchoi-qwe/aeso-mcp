# SPDX-License-Identifier: MIT
"""Credential-free HTTP client for the official AESO CSD archive on Box."""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
)

from aeso_mcp.config import Settings
from aeso_mcp.errors import DataValidationError, RateLimitError, UpstreamUnavailableError
from aeso_mcp.providers.http_utils import (
    buffer_response,
    is_retryable,
    parse_retry_after,
    retry_wait,
)

logger = logging.getLogger(__name__)

_ALLOWED_HOSTS = frozenset({"aeso.box.com", "aeso.app.box.com", "public.boxcloud.com"})
_MAX_REDIRECTS = 5
_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024


class AesoArchiveHttpClient:
    """Unauthenticated client restricted to the AESO-owned public Box share."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            headers={
                "Accept": "text/html,application/zip,*/*",
                "User-Agent": "aeso-mcp-csd-archive/0.1",
            },
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout_s,
                read=settings.http_read_timeout_s,
                write=settings.http_read_timeout_s,
                pool=settings.http_connect_timeout_s,
            ),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def get_text(self, url: str) -> str:
        response = await self._get(url)
        return response.text

    async def get_bytes(self, url: str) -> bytes:
        response = await self._get(url)
        return response.content

    async def _get(self, url: str) -> httpx.Response:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.http_max_retries + 1),
            wait=retry_wait,
            retry=retry_if_exception(is_retryable),
            reraise=True,
        ):
            with attempt:
                return await self._get_once(url)
        raise UpstreamUnavailableError("AESO CSD archive request failed after retries.")

    async def _get_once(self, url: str) -> httpx.Response:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            self._assert_allowed_url(current)
            try:
                async with self._client.stream("GET", current) as response:
                    status = response.status_code
                    logger.info(
                        "aeso_csd_archive_get host=%s path=%s status=%s",
                        response.url.host,
                        response.url.path,
                        status,
                    )
                    if status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location:
                            raise DataValidationError("AESO CSD archive redirect omitted Location.")
                        current = urljoin(str(response.url), location)
                        continue
                    if status == 429:
                        raise RateLimitError(
                            "AESO CSD archive rate limit exceeded.",
                            retry_after_s=parse_retry_after(response.headers.get("Retry-After")),
                        )
                    if status >= 500:
                        raise UpstreamUnavailableError(f"AESO CSD archive returned HTTP {status}.")
                    if status >= 400:
                        raise DataValidationError(
                            f"AESO CSD archive rejected the request (HTTP {status})."
                        )
                    return await buffer_response(
                        response, max_bytes=_MAX_ARCHIVE_BYTES, body_kind="AESO CSD archive"
                    )
            except httpx.TimeoutException as exc:
                raise UpstreamUnavailableError("AESO CSD archive request timed out.") from exc
            except httpx.TransportError as exc:
                raise UpstreamUnavailableError(
                    "Failed to connect to the AESO CSD archive."
                ) from exc

        raise DataValidationError("AESO CSD archive exceeded the redirect safety limit.")

    @staticmethod
    def _assert_allowed_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS:
            raise DataValidationError("CSD archive URL is outside the fixed HTTPS allow-list.")
        if parsed.username is not None or parsed.password is not None:
            raise DataValidationError("Credentials must not appear in CSD archive URLs.")
        query = parsed.query.lower()
        if any(marker in query for marker in ("api-key", "subscription-key", "aeso_api_key")):
            raise DataValidationError("Credentials must not appear in CSD archive URLs.")
