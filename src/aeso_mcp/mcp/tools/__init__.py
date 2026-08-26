# SPDX-License-Identifier: MIT
"""MCP tool registration package."""

from aeso_mcp.mcp.tools.analytics import register_analytics_tools
from aeso_mcp.mcp.tools.grid import register_grid_tools
from aeso_mcp.mcp.tools.history import register_history_tools
from aeso_mcp.mcp.tools.market import register_market_tools
from aeso_mcp.mcp.tools.market_power import register_market_power_tools
from aeso_mcp.mcp.tools.research import register_research_tools
from aeso_mcp.mcp.tools.reserves import register_reserve_tools

__all__ = [
    "register_analytics_tools",
    "register_grid_tools",
    "register_history_tools",
    "register_market_power_tools",
    "register_market_tools",
    "register_research_tools",
    "register_reserve_tools",
]
