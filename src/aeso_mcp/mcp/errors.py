# SPDX-License-Identifier: MIT
"""Translate domain errors into concise MCP tool failures."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps

from aeso_mcp.errors import AesoMcpError, ConfigurationError

logger = logging.getLogger(__name__)


def map_errors[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Wrap an async tool handler so domain errors become clean ValueErrors."""

    @wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except ConfigurationError:
            raise
        except AesoMcpError as exc:
            logger.info("tool_domain_error code=%s", exc.code)
            raise ValueError(exc.to_client_message()) from None
        except Exception:
            logger.exception("tool_unexpected_error")
            raise ValueError(
                "An unexpected error occurred while retrieving AESO data. "
                "Check server logs for details."
            ) from None

    return wrapper
