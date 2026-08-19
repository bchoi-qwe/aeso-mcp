# SPDX-License-Identifier: MIT
"""Domain-oriented MCP prompts for common AESO analysis workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.prompts.market import register_market_prompts

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = ["register_prompts"]


def register_prompts(mcp: FastMCP) -> None:
    """Register prompts that guide clients through repeatable market analyses."""
    register_market_prompts(mcp)
