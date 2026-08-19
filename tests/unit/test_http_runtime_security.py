# SPDX-License-Identifier: MIT
"""Focused tests for the HTTP-only runtime protections."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastmcp import FastMCP
from pydantic import SecretStr
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from aeso_mcp.config import Settings
from aeso_mcp.http_runtime import (
    RequestBodyLimitMiddleware,
    RequestConcurrencyMiddleware,
    build_http_runtime,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, Any] = {
        "aeso_api_key": SecretStr("test-key"),
        "http_allowed_hosts": ["testserver"],
        "http_allowed_origins": ["https://client.example"],
        "http_rate_limit_requests": 20,
        "http_max_request_body_bytes": 32,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _http_app(settings: Settings) -> httpx.ASGITransport:
    mcp = FastMCP(name="runtime-test")

    @mcp.custom_route("/echo", methods=["POST"], include_in_schema=False)
    async def echo(request: Request) -> JSONResponse:
        return JSONResponse({"body": (await request.body()).decode()})

    runtime = build_http_runtime(mcp, settings)
    app = mcp.http_app(
        transport="http",
        middleware=runtime.middleware,
        host_origin_protection=runtime.host_origin_protection,
        allowed_hosts=runtime.allowed_hosts,
        allowed_origins=runtime.allowed_origins,
    )
    return httpx.ASGITransport(app=app)


@pytest.mark.asyncio
async def test_http_runtime_protects_origin_auth_body_and_probe_routes() -> None:
    transport = _http_app(_settings(http_bearer_token=SecretStr("remote-token")))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}
        assert "remote-token" not in ready.text

        unauthorized = await client.post(
            "/echo",
            content=b"ok",
            headers={"Origin": "https://client.example"},
        )
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"

        forbidden_origin = await client.post(
            "/echo",
            content=b"ok",
            headers={
                "Authorization": "Bearer remote-token",
                "Origin": "https://not-allowed.example",
            },
        )
        assert forbidden_origin.status_code == 403

        allowed = await client.post(
            "/echo",
            content=b"ok",
            headers={
                "Authorization": "Bearer remote-token",
                "Origin": "https://client.example",
                "X-Request-ID": "test-request-1",
            },
        )
        assert allowed.status_code == 200
        assert allowed.json() == {"body": "ok"}
        assert allowed.headers["x-request-id"] == "test-request-1"

        too_large = await client.post(
            "/echo",
            content=b"012345678901234567890123456789012",
            headers={
                "Authorization": "Bearer remote-token",
                "Origin": "https://client.example",
            },
        )
        assert too_large.status_code == 413

        health_from_other_origin = await client.get(
            "/healthz",
            headers={"Origin": "https://not-allowed.example"},
        )
        assert health_from_other_origin.status_code == 403


@pytest.mark.asyncio
async def test_http_runtime_rate_limits_each_client() -> None:
    transport = _http_app(
        _settings(
            http_rate_limit_requests=1,
            http_bearer_token=SecretStr("remote-token"),
        )
    )
    headers = {
        "Authorization": "Bearer remote-token",
        "Origin": "https://client.example",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.post("/echo", content=b"one", headers=headers)
        second = await client.post("/echo", content=b"two", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"


@pytest.mark.asyncio
async def test_http_runtime_rejects_excess_concurrency_without_waiting() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    responses: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        del scope, receive
        started.set()
        await release.wait()
        del send

    middleware = RequestConcurrencyMiddleware(app, max_concurrent=1)

    async def first_request() -> None:
        async def send(message: Message) -> None:
            responses.append(message)

        await middleware(
            {"type": "http", "path": "/mcp"},
            _empty_receive,
            send,
        )

    first = asyncio.create_task(first_request())
    await started.wait()

    second_responses: list[Message] = []

    async def second_send(message: Message) -> None:
        second_responses.append(message)

    await middleware(
        {"type": "http", "path": "/mcp"},
        _empty_receive,
        second_send,
    )
    release.set()
    await first

    assert second_responses[0]["status"] == 503
    assert responses == []


@pytest.mark.asyncio
async def test_body_limit_replays_body_then_preserves_disconnect() -> None:
    received: list[Message] = []
    upstream: list[Message] = [
        {"type": "http.request", "body": b"payload", "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return upstream.pop(0)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        del scope, send
        received.append(await receive())
        received.append(await receive())

    middleware = RequestBodyLimitMiddleware(app, max_bytes=32)
    await middleware(
        {"type": "http", "path": "/mcp", "headers": []},
        receive,
        _empty_send,
    )

    assert received == [
        {"type": "http.request", "body": b"payload", "more_body": False},
        {"type": "http.disconnect"},
    ]


async def _empty_receive() -> Message:
    return {"type": "http.disconnect"}


async def _empty_send(_message: Message) -> None:
    return None
