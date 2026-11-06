# SPDX-License-Identifier: MIT
"""Shared bounded response buffering and upstream retry policy."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite

import httpx
from tenacity import RetryCallState, wait_exponential_jitter

from aeso_mcp.errors import DataValidationError, RateLimitError, UpstreamUnavailableError

_MAX_AUTOMATIC_RETRY_AFTER_S = 60.0
_FALLBACK_RETRY_WAIT = wait_exponential_jitter(initial=0.5, max=8.0)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError) and exc.retry_after_s is not None:
        # Do not retry before a long server-requested delay. Return the error and
        # its retry_after_s field so the caller can schedule the retry instead.
        return exc.retry_after_s <= _MAX_AUTOMATIC_RETRY_AFTER_S
    return isinstance(exc, RateLimitError | UpstreamUnavailableError | httpx.TransportError)


def retry_wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, RateLimitError) and exc.retry_after_s is not None:
        return max(exc.retry_after_s, 0.0)
    return float(_FALLBACK_RETRY_WAIT(retry_state))


def parse_retry_after(value: str | None) -> float | None:
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
    return max(seconds, 0.0) if isfinite(seconds) else None


async def buffer_response(
    response: httpx.Response, *, max_bytes: int, body_kind: str
) -> httpx.Response:
    """Bound decoded bytes and return a response that will not decode them twice."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            limit = (
                f"{max_bytes // (1024 * 1024)} MiB"
                if max_bytes % (1024 * 1024) == 0
                else f"{max_bytes:,} bytes"
            )
            raise DataValidationError(f"{body_kind} exceeded the {limit} safety limit.")
        chunks.append(chunk)
    headers = response.headers.copy()
    # aiter_bytes already decoded Content-Encoding; these describe the wire body.
    headers.pop("content-encoding", None)
    headers.pop("content-length", None)
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=b"".join(chunks),
        request=response.request,
    )
