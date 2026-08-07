# SPDX-License-Identifier: MIT
"""Domain error hierarchy for AESO MCP."""

from __future__ import annotations


class AesoMcpError(Exception):
    """Base error for AESO MCP."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__

    def to_client_message(self) -> str:
        """Concise message safe to return to MCP clients (no secrets)."""
        return self.message


class ConfigurationError(AesoMcpError):
    """Missing or invalid configuration."""


class AuthenticationError(AesoMcpError):
    """Upstream rejected credentials (401/403)."""


class RateLimitError(AesoMcpError):
    """Upstream rate limited the request (429)."""

    def __init__(
        self,
        message: str = "AESO API rate limit exceeded. Retry after a short delay.",
        *,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class UpstreamUnavailableError(AesoMcpError):
    """Transient upstream failure (5xx / network)."""


class InvalidDateRangeError(AesoMcpError):
    """Client requested an invalid or unbounded date range."""


class UnsupportedDatasetError(AesoMcpError):
    """Requested dataset is not available through implemented providers."""


class DataValidationError(AesoMcpError):
    """Upstream payload failed validation/normalization."""


class QueryTooLargeError(InvalidDateRangeError):
    """Request would return more data than server safety limits allow."""
