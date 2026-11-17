# SPDX-License-Identifier: MIT
"""FastMCP adapter: construct and configure the MCP server.

Framework-specific code lives here. Domain services remain FastMCP-agnostic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from aeso_mcp import __version__
from aeso_mcp.app import AppContainer, build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.prompts import register_prompts
from aeso_mcp.mcp.resources import register_resources
from aeso_mcp.mcp.skills import register_market_research_skill
from aeso_mcp.mcp.tools import (
    register_analytics_tools,
    register_discovery_tools,
    register_grid_tools,
    register_history_tools,
    register_market_power_tools,
    register_market_tools,
    register_report_tools,
    register_research_data_tools,
    register_research_tools,
    register_reserve_tools,
)
from aeso_mcp.mcp.tools.operations import register_operations_tools


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

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await container.aclose()

    mcp = FastMCP(
        name="aeso-mcp",
        version=__version__,
        instructions=(
            "This AESO MCP server supports public-report tools without credentials; "
            "authenticated APIM data requires AESO_API_KEY. It provides Alberta electricity "
            "market data and deterministic analytics from official AESO APIs and an internal "
            "source-specific client for allow-listed public reports; that client never sends "
            "AESO_API_KEY to ets.aeso.ca. Prefer "
            "get_market_snapshot for current conditions, get_pool_prices for hourly CAD/MWh "
            "history, transmission outage tools for planned grid work, and market-power tools "
            "for MCSINR / secondary offer cap status. All market timestamps use "
            "America/Edmonton. This project is independent and not affiliated with or "
            "endorsed by AESO."
        ),
        lifespan=lifespan,
        # The catalog and static domain resources are identical for all clients.
        # MCP 2026-07-28 clients can reuse them for five minutes.
        cache_ttl=300,
        cache_scope="public",
    )

    register_discovery_tools(mcp)
    register_market_tools(mcp, container)
    register_grid_tools(mcp, container)
    register_market_power_tools(mcp, container)
    register_analytics_tools(mcp, container)
    register_operations_tools(mcp, container)
    register_history_tools(mcp, container)
    register_research_tools(mcp, container)
    register_research_data_tools(mcp, container)
    register_report_tools(mcp, container)
    register_reserve_tools(mcp, container)
    register_prompts(mcp)
    register_resources(mcp)
    register_market_research_skill(mcp)
    if container.settings.tool_discovery_mode == "compact":
        from fastmcp.server.transforms.search import BM25SearchTransform

        # Search returns complete tool schemas and call_tool can invoke a hidden
        # tool; keep the three most common entry points immediately callable.
        mcp.add_transform(
            BM25SearchTransform(
                max_results=5,
                always_visible=[
                    "get_market_snapshot",
                    "analyze_market_event",
                    "get_forecast",
                ],
            )
        )
    return mcp
