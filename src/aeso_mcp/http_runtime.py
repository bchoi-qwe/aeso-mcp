# SPDX-License-Identifier: MIT
"""HTTP-only runtime protections for the FastMCP transport.

The domain application is intentionally unaware of these controls.  This
module provides small ASGI middleware components that can be passed to
FastMCP's HTTP transport, while the stdio transport remains untouched.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import math
import re
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Iterable
from dataclasses import dataclass

from fastmcp import FastMCP
from pydantic import SecretStr
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aeso_mcp.config import Settings
from aeso_mcp.errors import ConfigurationError

_PROBE_PATHS = frozenset({"/healthz", "/readyz"})
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

logger = logging.getLogger(__name__)


def _json_error(
    status_code: int,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = dict(_NO_STORE_HEADERS)
    if headers:
        response_headers.update(headers)
    return JSONResponse({"detail": detail}, status_code=status_code, headers=response_headers)


class BearerAuthMiddleware:
    """Require an exact bearer token when one is configured."""

    def __init__(
        self,
        app: ASGIApp,
        token: str | SecretStr,
        exempt_paths: Iterable[str] = _PROBE_PATHS,
    ) -> None:
        self.app = app
        self._token = token.get_secret_value() if isinstance(token, SecretStr) else token
        self._exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt_paths:
            await self.app(scope, receive, send)
            return
        if not self._authorized(scope):
            response = _json_error(
                401,
                "Bearer authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        authorization = Headers(scope=scope).get("authorization", "")
        scheme, separator, credentials = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not credentials:
            return False
        return hmac.compare_digest(credentials, self._token)


class RequestObservabilityMiddleware:
    """Emit secret-free HTTP request metrics and return a correlation identifier."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        request_id = self._request_id(scope)
        status = 500

        async def observed_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            logger.info(
                "http_request request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
                request_id,
                scope.get("method", ""),
                scope.get("path", ""),
                status,
                (time.monotonic() - started) * 1000,
            )

    @staticmethod
    def _request_id(scope: Scope) -> str:
        supplied = Headers(scope=scope).get("x-request-id", "")
        if _REQUEST_ID_RE.fullmatch(supplied):
            return supplied
        return uuid.uuid4().hex


class ClientRateLimitMiddleware:
    """Bound requests per client using a small in-memory sliding window."""

    def __init__(
        self,
        app: ASGIApp,
        requests: int,
        window_s: float,
        max_clients: int = 4096,
        exempt_paths: Iterable[str] = _PROBE_PATHS,
    ) -> None:
        self.app = app
        self._requests = requests
        self._window_s = window_s
        self._max_clients = max_clients
        self._exempt_paths = frozenset(exempt_paths)
        self._clients: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt_paths:
            await self.app(scope, receive, send)
            return

        allowed, retry_after = await self._allow(self._client_key(scope))
        if not allowed:
            response = _json_error(
                429,
                "Request rate limit exceeded.",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self._requests),
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _allow(self, client_key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self._window_s
        async with self._lock:
            timestamps = self._clients.get(client_key)
            if timestamps is None:
                if len(self._clients) >= self._max_clients:
                    self._clients.popitem(last=False)
                timestamps = deque()
                self._clients[client_key] = timestamps
            else:
                self._clients.move_to_end(client_key)

            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self._requests:
                retry_after = max(1, math.ceil(timestamps[0] + self._window_s - now))
                return False, retry_after
            timestamps.append(now)
            return True, 0

    @staticmethod
    def _client_key(scope: Scope) -> str:
        client = scope.get("client")
        if isinstance(client, tuple) and client:
            return str(client[0])
        return "unknown"


class RequestConcurrencyMiddleware:
    """Reject excess concurrent HTTP requests before they reach MCP tools."""

    def __init__(
        self,
        app: ASGIApp,
        max_concurrent: int,
        exempt_paths: Iterable[str] = _PROBE_PATHS,
    ) -> None:
        self.app = app
        self._max_concurrent = max_concurrent
        self._exempt_paths = frozenset(exempt_paths)
        self._active = 0
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt_paths:
            await self.app(scope, receive, send)
            return

        async with self._lock:
            at_capacity = self._active >= self._max_concurrent
            if not at_capacity:
                self._active += 1
        if at_capacity:
            response = _json_error(
                503,
                "Server concurrency limit reached.",
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            async with self._lock:
                self._active -= 1


class RequestBodyLimitMiddleware:
    """Reject HTTP request bodies larger than the configured byte bound."""

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        exempt_paths: Iterable[str] = _PROBE_PATHS,
    ) -> None:
        self.app = app
        self._max_bytes = max_bytes
        self._exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt_paths:
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                response = _json_error(400, "Invalid Content-Length header.")
                await response(scope, receive, send)
                return
            if declared_length < 0:
                response = _json_error(400, "Invalid Content-Length header.")
                await response(scope, receive, send)
                return
            if declared_length > self._max_bytes:
                response = _json_error(413, "Request body exceeds the configured limit.")
                await response(scope, receive, send)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, self._replay_receive(message, receive), send)
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self._max_bytes:
                response = _json_error(413, "Request body exceeds the configured limit.")
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        await self.app(scope, self._replay_receive(bytes(body), receive), send)

    @staticmethod
    def _replay_receive(message: Message | bytes, downstream_receive: Receive) -> Receive:
        sent = False

        async def receive() -> Message:
            nonlocal sent
            if sent:
                return await downstream_receive()
            sent = True
            if isinstance(message, bytes):
                return {"type": "http.request", "body": message, "more_body": False}
            return message

        return receive


def _probe_response(status: str) -> JSONResponse:
    return JSONResponse({"status": status}, headers=_NO_STORE_HEADERS)


def register_probe_routes(mcp: FastMCP) -> None:
    """Register secret-free liveness and readiness routes on a FastMCP server."""

    @mcp.custom_route("/healthz", methods=["GET"], name="healthz", include_in_schema=False)
    async def healthz(_request: Request) -> Response:
        return _probe_response("ok")

    @mcp.custom_route("/readyz", methods=["GET"], name="readyz", include_in_schema=False)
    async def readyz(_request: Request) -> Response:
        return _probe_response("ready")


@dataclass(frozen=True)
class HttpRuntime:
    """FastMCP HTTP transport options assembled from application settings."""

    middleware: list[Middleware]
    allowed_hosts: list[str] | None
    allowed_origins: list[str] | None
    host_origin_protection: bool = True


def is_loopback_bind(host: str) -> bool:
    """Return whether an HTTP bind target is local-only.

    Hostnames that cannot be resolved safely at startup are treated as remote.
    This keeps an unusual hostname from accidentally bypassing the remote
    authentication policy; DNS resolution is intentionally not performed here.
    """
    candidate = host.strip().lower().rstrip(".")
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def validate_http_bind(settings: Settings, host: str) -> None:
    """Reject an unauthenticated non-loopback HTTP bind by default.

    Stdio is unaffected because it never calls this HTTP-only boundary.
    ``http_allow_insecure_remote`` is intentionally explicit and defaults to
    false so a deployment cannot become publicly reachable by accident.
    """
    if is_loopback_bind(host):
        return
    if settings.http_bearer_token is not None:
        return
    if settings.http_allow_insecure_remote:
        return
    raise ConfigurationError(
        "Non-loopback HTTP binding requires AESO_MCP_HTTP_BEARER_TOKEN. "
        "Set a bearer token or explicitly set AESO_MCP_HTTP_ALLOW_INSECURE_REMOTE=true."
    )


def build_http_runtime(
    mcp: FastMCP,
    settings: Settings,
    *,
    host: str = "127.0.0.1",
) -> HttpRuntime:
    """Register probes and return strict, bounded HTTP transport options."""
    validate_http_bind(settings, host)
    register_probe_routes(mcp)
    # Admission controls run before authentication so unauthenticated clients
    # cannot bypass the request and concurrency bounds while probing tokens.
    middleware: list[Middleware] = [
        Middleware(RequestObservabilityMiddleware),
        Middleware(
            ClientRateLimitMiddleware,
            requests=settings.http_rate_limit_requests,
            window_s=settings.http_rate_limit_window_s,
        ),
        Middleware(
            RequestConcurrencyMiddleware,
            max_concurrent=settings.http_max_concurrent_requests,
        ),
    ]
    if settings.http_bearer_token is not None:
        middleware.append(
            Middleware(
                BearerAuthMiddleware,
                token=settings.http_bearer_token,
            )
        )
    middleware.append(
        Middleware(
            RequestBodyLimitMiddleware,
            max_bytes=settings.http_max_request_body_bytes,
        )
    )
    return HttpRuntime(
        middleware=middleware,
        allowed_hosts=settings.http_allowed_hosts,
        allowed_origins=settings.http_allowed_origins,
    )


__all__ = [
    "BearerAuthMiddleware",
    "ClientRateLimitMiddleware",
    "HttpRuntime",
    "RequestBodyLimitMiddleware",
    "RequestConcurrencyMiddleware",
    "RequestObservabilityMiddleware",
    "build_http_runtime",
    "is_loopback_bind",
    "register_probe_routes",
    "validate_http_bind",
]
