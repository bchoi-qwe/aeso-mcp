# SPDX-License-Identifier: MIT
"""MCP tool registration package."""

from aeso_mcp.mcp.tools.analytics import register_analytics_tools
from aeso_mcp.mcp.tools.grid import register_grid_tools
from aeso_mcp.mcp.tools.market import register_market_tools

__all__ = [
    "register_analytics_tools",
    "register_grid_tools",
    "register_market_tools",
]
