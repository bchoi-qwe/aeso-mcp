# SPDX-License-Identifier: MIT
"""Regression tests at real HTTPX streaming and retry boundaries."""

from __future__ import annotations

import gzip
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
import respx
from pydantic import SecretStr

from aeso_mcp.config import Settings
from aeso_mcp.errors import DataValidationError, RateLimitError
from aeso_mcp.providers.archive_http import AesoArchiveHttpClient
from aeso_mcp.providers.http import AesoHttpClient
from aeso_mcp.providers.http_utils import parse_retry_after
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient

CLIENTS = [
    (AesoArchiveHttpClient, "https://aeso.box.com/report"),
    (AesoPublicReportsHttpClient, "https://ets.aeso.ca/report"),
]


class BodyStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize(("client_type", "url"), CLIENTS)
@respx.mock
async def test_compressed_response_is_decoded_once(client_type, url) -> None:
    content = b"hour,price\n1,50\n"
    stream = BodyStream([gzip.compress(content)])
    route = respx.get(url).respond(200, headers={"Content-Encoding": "gzip"}, stream=stream)
    client = client_type(Settings(aeso_api_key=SecretStr("test-key")))
    try:
        assert await client.get_bytes(url) == content
        assert stream.closed
        assert "API-KEY" not in route.calls[0].request.headers
    finally:
        await client.aclose()


@pytest.mark.parametrize(("client_type", "url"), CLIENTS)
@pytest.mark.parametrize("method", ["get_text", "get_bytes"])
@respx.mock
async def test_oversize_stream_stops_and_closes(client_type, url, method, monkeypatch) -> None:
    monkeypatch.setattr("aeso_mcp.providers.archive_http._MAX_ARCHIVE_BYTES", 4)
    monkeypatch.setattr("aeso_mcp.providers.public_reports_http._MAX_PUBLIC_REPORT_BYTES", 4)
    stream = BodyStream([b"1234", b"5", b"must-not-be-read"])
    respx.get(url).respond(200, stream=stream)
    client = client_type(Settings(aeso_api_key=SecretStr("test-key")))
    try:
        with pytest.raises(DataValidationError, match="4 bytes"):
            await getattr(client, method)(url)
        assert stream.consumed == 2
        assert stream.closed
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("client_type", "url"),
    [*CLIENTS, (AesoHttpClient, "https://apimgw.aeso.ca/public/report")],
)
@respx.mock
async def test_short_retry_after_retries_and_long_delay_returns(client_type, url) -> None:
    client = client_type(Settings(aeso_api_key=SecretStr("test-key"), http_max_retries=1))
    route = respx.get(url).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async def fetch():
        if isinstance(client, AesoHttpClient):
            return await client.get_json("report")
        return await client.get_bytes(url)

    try:
        await fetch()
        assert route.call_count == 2
        route.mock(return_value=httpx.Response(429, headers={"Retry-After": "120"}))
        with pytest.raises(RateLimitError) as error:
            await fetch()
        assert error.value.retry_after_s == 120
        assert route.call_count == 3
    finally:
        await client.aclose()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "garbage", ""])
def test_invalid_retry_delay_is_ignored(value) -> None:
    assert parse_retry_after(value) is None


def test_retry_after_http_date() -> None:
    future = datetime.now(UTC) + timedelta(seconds=30)
    delay = parse_retry_after(format_datetime(future, usegmt=True))
    assert delay is not None and 28 <= delay <= 30
