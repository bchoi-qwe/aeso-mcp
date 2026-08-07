# SPDX-License-Identifier: MIT
"""Grid MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.assets import AssetsRequest, AssetsResponse
from aeso_mcp.models.grid import (
    InterchangeResponse,
    OutagesRequest,
    OutagesResponse,
    ReservesResponse,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from aeso_mcp.app import AppContainer


def register_grid_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register interchange, reserves, outages, and assets tools."""

    @mcp.tool(
        name="get_interchange",
        description=(
            "Returns current Alberta interchange flows by path in MW, including net interchange. "
            "Positive/negative path signs follow AESO Current Supply Demand conventions."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_interchange() -> InterchangeResponse:
        return await container.grid.get_interchange()

    @mcp.tool(
        name="get_reserves",
        description=(
            "Returns current AESO operating reserve indicators in MW, including contingency "
            "reserve required/dispatched and fast frequency response volumes when published."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_reserves() -> ReservesResponse:
        return await container.grid.get_reserves()

    @mcp.tool(
        name="get_outages",
        description=(
            "Returns AESO generator outage observations for [start, end) when available. "
            "Units: MW. Timestamps: America/Edmonton. Empty results may indicate no outages "
            "or upstream unavailability."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_outages(request: OutagesRequest) -> OutagesResponse:
        return await container.grid.get_outages(request)

    @mcp.tool(
        name="get_assets",
        description=(
            "Returns AESO market asset registry records with optional filters for asset ID, "
            "pool participant, operating status, and asset type. Results may be truncated "
            "by the limit parameter."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_assets(request: AssetsRequest) -> AssetsResponse:
        return await container.assets.get_assets(request)
