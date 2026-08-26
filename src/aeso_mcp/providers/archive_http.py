# SPDX-License-Identifier: MIT
"""Credential-free HTTP client for the official AESO CSD archive on Box."""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from aeso_mcp.config import Settings
from aeso_mcp.errors import DataValidationError, RateLimitError, UpstreamUnavailableError

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
        content = response.content
        if len(content) > _MAX_ARCHIVE_BYTES:
            raise DataValidationError("AESO CSD archive exceeded the 100 MiB safety limit.")
        return content

    async def _get(self, url: str) -> httpx.Response:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.http_max_retries + 1),
            wait=wait_exponential_jitter(initial=0.5, max=8.0),
            retry=retry_if_exception_type(UpstreamUnavailableError),
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
                response = await self._client.get(current)
            except httpx.TimeoutException as exc:
                raise UpstreamUnavailableError("AESO CSD archive request timed out.") from exc
            except httpx.TransportError as exc:
                raise UpstreamUnavailableError(
                    "Failed to connect to the AESO CSD archive."
                ) from exc

            logger.info(
                "aeso_csd_archive_get host=%s path=%s status=%s",
                response.url.host,
                response.url.path,
                response.status_code,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise DataValidationError("AESO CSD archive redirect omitted Location.")
                current = urljoin(str(response.url), location)
                continue
            if response.status_code == 429:
                raise RateLimitError("AESO CSD archive rate limit exceeded.")
            if response.status_code >= 500:
                raise UpstreamUnavailableError(
                    f"AESO CSD archive returned HTTP {response.status_code}."
                )
            if response.status_code >= 400:
                raise DataValidationError(
                    f"AESO CSD archive rejected the request (HTTP {response.status_code})."
                )
            return response
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
