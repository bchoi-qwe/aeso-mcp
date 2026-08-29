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

# Only official hosts used by the named report providers. Do not add a generic
# or user-controlled host surface; the aeso.ca host is needed only to resolve
# the AESO-linked yearly wind/solar actual-vs-forecast files.
ALLOWED_PUBLIC_REPORT_HOSTS = frozenset({"ets.aeso.ca", "aeso.ca", "www.aeso.ca"})
_MAX_REDIRECTS = 5
_MAX_PUBLIC_REPORT_BYTES = 25 * 1024 * 1024
# The largest verified fixed frequency asset is the 2023 raw CSV at 192,445,819
# bytes; 192 MiB covers it and the 2025 148,813,587-byte file with a small
# margin without allowing an arbitrary large response through this path.
_MAX_PUBLIC_ASSET_BYTES = 192 * 1024 * 1024


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
            # Manual redirect following so every Location is allow-list checked.
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        if self._client.is_closed:
            return
        await self._client.aclose()

    async def get_text(self, url: str) -> str:
        """GET an allow-listed absolute URL and return response text."""
        self._assert_allowed_url(url)
        response = await self._get(url)
        _validate_report_body(response.content)
        return response.text

    async def get_bytes(self, url: str) -> bytes:
        """GET an allow-listed absolute URL and return raw bytes."""
        self._assert_allowed_url(url)
        response = await self._get(url)
        _validate_report_body(response.content)
        return response.content

    async def get_binary_asset(self, url: str) -> bytes:
        """GET one official AESO downloadable asset without text-body assumptions.

        The data-request archive publishes XLSX/ZIP assets as well as CSV files.
        Those files are still fetched through the same credential-free, manually
        redirected client, but cannot pass the normal report-body NUL check.  Keep
        this method deliberately narrower than :meth:`get_bytes`: only HTTPS
        ``www.aeso.ca/assets/Uploads/`` assets with a known archive/text suffix
        are accepted, and the compressed response is bounded.
        """
        self._assert_binary_asset_url(url)
        response = await self._get(url)
        # Redirects are followed by the shared client only after the general
        # AESO host allow-list check. Re-apply the narrower asset boundary to
        # the final URL so a fixed asset cannot redirect to an unrelated public
        # endpoint on another allow-listed host.
        self._assert_binary_asset_url(str(response.url))
        _validate_binary_asset_body(response.content)
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
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            self._assert_allowed_url(current)
            try:
                response = await self._client.get(current)
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
            if status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise DataValidationError(
                        f"AESO public report redirect missing Location (HTTP {status})."
                    )
                current = urljoin(str(response.url), location)
                continue
            if status == 404:
                raise DataValidationError(f"AESO public report not found: {path}")
            if status == 429:
                retry_after = response.headers.get("Retry-After")
                retry_s = float(retry_after) if retry_after and retry_after.isdigit() else None
                raise RateLimitError(retry_after_s=retry_s)
            if status >= 500:
                raise UpstreamUnavailableError(f"AESO public report returned HTTP {status}.")
            if status >= 400:
                raise DataValidationError(
                    f"AESO public report rejected the request (HTTP {status})."
                )
            return response
        raise DataValidationError(
            f"AESO public report exceeded {_MAX_REDIRECTS} redirects while staying allow-listed."
        )

    def _assert_allowed_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise DataValidationError("Public report URLs must be http(s).")
        if parsed.username is not None or parsed.password is not None:
            raise DataValidationError("Credentials must not appear in public report URLs.")
        host = (parsed.hostname or "").lower()
        if host not in ALLOWED_PUBLIC_REPORT_HOSTS:
            raise DataValidationError(f"Upstream host not allow-listed for public reports: {host}")
        # Reject credential leakage patterns in query strings.
        query = (parsed.query or "").lower()
        if "api-key" in query or "subscription-key" in query or "aeso_api_key" in query:
            raise DataValidationError("Credentials must not appear in public report URLs.")

    def _assert_binary_asset_url(self, url: str) -> None:
        self._assert_allowed_url(url)
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path
        suffix = path.rsplit("/", 1)[-1].lower().rsplit(".", 1)[-1] if "." in path else ""
        if host != "www.aeso.ca" or not path.startswith("/assets/Uploads/"):
            raise DataValidationError(
                "Binary public assets must be fixed files under www.aeso.ca/assets/Uploads/."
            )
        if parsed.query or parsed.fragment or suffix not in {"csv", "xlsx", "zip"}:
            raise DataValidationError("Binary public assets must use a known static file URL.")


def _validate_report_body(content: bytes) -> None:
    if len(content) > _MAX_PUBLIC_REPORT_BYTES:
        raise DataValidationError("AESO public report exceeded the 25 MiB safety limit.")
    if b"\x00" in content[:4096]:
        raise DataValidationError("AESO public report returned unexpected binary content.")


def _validate_binary_asset_body(content: bytes) -> None:
    if len(content) > _MAX_PUBLIC_ASSET_BYTES:
        raise DataValidationError("AESO public asset exceeded the 192 MiB safety limit.")
    if not content:
        raise DataValidationError("AESO public asset returned an empty body.")
    # XLSX and ZIP files are ZIP containers.  CSV assets remain text and are
    # accepted without a NUL check here because a fixed source parser validates
    # their header and numeric fields before returning observations.
    if content[:2] == b"PK":
        return
    if b"\x00" in content[:4096]:
        raise DataValidationError("AESO public asset returned unexpected binary content.")
