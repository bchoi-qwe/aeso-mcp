# SPDX-License-Identifier: MIT
"""FastMCP adapter: construct and configure the MCP server.

Framework-specific code lives here. Domain services remain FastMCP-agnostic.
"""

from __future__ import annotations

from fastmcp import FastMCP

from aeso_mcp import __version__
from aeso_mcp.app import AppContainer, build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.resources import register_resources
from aeso_mcp.mcp.tools import (
    register_analytics_tools,
    register_grid_tools,
    register_market_power_tools,
    register_market_tools,
)


def create_mcp_server(
    settings: Settings | None = None,
    container: AppContainer | None = None,
) -> FastMCP:
    """Create a configured FastMCP server instance."""
    if container is None:
        if settings is None:
            from aeso_mcp.config import get_settings

            settings = get_settings()
        container = build_container(settings)

    mcp = FastMCP(
        name="aeso-mcp",
        version=__version__,
        instructions=(
            "AESO MCP provides Alberta electricity market data and deterministic analytics "
            "from official AESO APIs and allow-listed public reports. Prefer "
            "get_market_snapshot for current conditions, get_pool_prices for hourly CAD/MWh "
            "history, transmission outage tools for planned grid work, and market-power tools "
            "for MCSINR / secondary offer cap status. All market timestamps use "
            "America/Edmonton. This project is independent and not affiliated with or "
            "endorsed by AESO."
        ),
    )

    register_market_tools(mcp, container)
    register_grid_tools(mcp, container)
    register_market_power_tools(mcp, container)
    register_analytics_tools(mcp, container)
    register_resources(mcp)
    return mcp
