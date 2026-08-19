# SPDX-License-Identifier: MIT
"""Translate domain errors into concise MCP tool failures."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from functools import wraps

from aeso_mcp.errors import AesoMcpError, RateLimitError

logger = logging.getLogger(__name__)


def map_errors[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Wrap an async tool handler so domain errors become clean ValueErrors."""

    @wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except AesoMcpError as exc:
            logger.info("tool_domain_error code=%s", exc.code)
            raise ValueError(_client_error_json(exc)) from None
        except Exception:
            logger.exception("tool_unexpected_error")
            raise ValueError(_unexpected_error_json()) from None

    return wrapper


def _client_error_json(exc: AesoMcpError) -> str:
    """Serialize a safe, stable domain error envelope for MCP clients."""
    error: dict[str, object] = {
        "code": exc.code,
        "message": exc.to_client_message(),
    }
    if isinstance(exc, RateLimitError) and exc.retry_after_s is not None:
        error["retry_after_s"] = exc.retry_after_s
    return json.dumps({"error": error}, ensure_ascii=True, separators=(",", ":"))


def _unexpected_error_json() -> str:
    return json.dumps(
        {
            "error": {
                "code": "internal_error",
                "message": (
                    "An unexpected error occurred while retrieving AESO data. "
                    "Check server logs for details."
                ),
            }
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
