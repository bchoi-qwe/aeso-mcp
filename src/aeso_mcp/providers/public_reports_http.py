# SPDX-License-Identifier: MIT
"""Credential-free HTTP client for AESO public report hosts.

Never send ``AESO_API_KEY`` through this client. Paths must be known report
endpoints resolved by provider methods — not arbitrary user-supplied URLs.
"""

from __future__ import annotations

import logging
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
from aeso_mcp.errors import DataValidationError, RateLimitError, UpstreamUnavailableError

logger = logging.getLogger(__name__)

ALLOWED_PUBLIC_REPORT_HOSTS = frozenset({"ets.aeso.ca", "itc.aeso.ca"})


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RateLimitError | UpstreamUnavailableError | httpx.TransportError)


def _before_sleep(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "aeso_public_report_retry attempt=%s error=%s",
        retry_state.attempt_number,
        type(exc).__name__ if exc else "unknown",
    )


class AesoPublicReportsHttpClient:
    """Unauthenticated httpx client for allow-listed AESO public report hosts."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            headers={
                "Cache-Control": "no-cache",
                "Accept": "text/html,text/csv,text/plain,*/*",
                "User-Agent": "aeso-mcp-public-reports/0.1",
            },
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout_s,
                read=settings.http_read_timeout_s,
                write=settings.http_read_timeout_s,
                pool=settings.http_connect_timeout_s,
            ),
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_text(self, url: str) -> str:
        """GET an allow-listed absolute URL and return response text."""
        self._assert_allowed_url(url)
        response = await self._get(url)
        return response.text

    async def get_bytes(self, url: str) -> bytes:
        """GET an allow-listed absolute URL and return raw bytes."""
        self._assert_allowed_url(url)
        response = await self._get(url)
        return response.content

    def resolve_outage_report_url(self, href: str, *, base: str) -> str:
        """Normalize AESO outage-report relative/backslash hrefs to an absolute URL."""
        cleaned = href.replace("\\", "/").strip()
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            url = cleaned
        elif cleaned.startswith("file:///"):
            filename = cleaned.rsplit("/", 1)[-1]
            url = f"http://ets.aeso.ca/outage_reports/csvData/{filename}"
        elif cleaned.startswith("../"):
            url = f"http://ets.aeso.ca/outage_reports/{cleaned[3:]}"
        elif cleaned.startswith("csvData/") or cleaned.startswith("archives/"):
            url = f"http://ets.aeso.ca/outage_reports/{cleaned}"
        else:
            url = urljoin(base if base.endswith("/") else base + "/", cleaned)
        self._assert_allowed_url(url)
        return url

    async def _get(self, url: str) -> httpx.Response:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.http_max_retries + 1),
            wait=wait_exponential_jitter(initial=0.5, max=8.0),
            retry=retry_if_exception(_is_retryable),
            before_sleep=_before_sleep,
            reraise=True,
        ):
            with attempt:
                return await self._get_once(url)
        raise UpstreamUnavailableError("AESO public report request failed after retries.")

    async def _get_once(self, url: str) -> httpx.Response:
        try:
            response = await self._client.get(url)
        except httpx.TimeoutException as exc:
            raise UpstreamUnavailableError("AESO public report request timed out.") from exc
        except httpx.TransportError as exc:
            raise UpstreamUnavailableError("Failed to connect to AESO public reports.") from exc

        status = response.status_code
        path = urlparse(str(response.url)).path
        logger.info(
            "aeso_public_report_get path=%s status=%s duration_ms=%.1f",
            path,
            status,
            response.elapsed.total_seconds() * 1000,
        )
        if status == 404:
            raise DataValidationError(f"AESO public report not found: {path}")
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            retry_s = float(retry_after) if retry_after and retry_after.isdigit() else None
            raise RateLimitError(retry_after_s=retry_s)
        if status >= 500:
            raise UpstreamUnavailableError(f"AESO public report returned HTTP {status}.")
        if status >= 400:
            raise DataValidationError(f"AESO public report rejected the request (HTTP {status}).")
        return response

    def _assert_allowed_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise DataValidationError("Public report URLs must be http(s).")
        host = (parsed.hostname or "").lower()
        if host not in ALLOWED_PUBLIC_REPORT_HOSTS:
            raise DataValidationError(f"Upstream host not allow-listed for public reports: {host}")
        # Reject credential leakage patterns in query strings.
        query = (parsed.query or "").lower()
        if "api-key" in query or "subscription-key" in query or "aeso_api_key" in query:
            raise DataValidationError("Credentials must not appear in public report URLs.")
