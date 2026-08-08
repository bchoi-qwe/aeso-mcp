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
from aeso_mcp.models.transmission import (
    ApprovedTransmissionOutagesRequest,
    LongRangeTransmissionOutagesRequest,
    TransmissionOutagesResponse,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from aeso_mcp.app import AppContainer


def register_grid_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register interchange, reserves, outages, assets, and transmission tools."""

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
            "Returns hourly AESO generator outage capacity by technology/fuel for "
            "[start, end) (Total Outage MW plus per-fuel components). Timestamps: "
            "America/Edmonton. For transmission planned outages use "
            "get_approved_transmission_outages or get_long_range_transmission_outages."
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
        name="get_approved_transmission_outages",
        description=(
            "Returns AESO-approved planned transmission outages (approval_status=approved). "
            "Omit start/end for the current public publication. Historical start/end select "
            "publication windows and are tightly bounded. Distinct from generator outages "
            "and from long-range tentative outages."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_approved_transmission_outages(
        request: ApprovedTransmissionOutagesRequest,
    ) -> TransmissionOutagesResponse:
        return await container.transmission.get_approved_transmission_outages(request)

    @mcp.tool(
        name="get_long_range_transmission_outages",
        description=(
            "Returns Long Range Significant Transmission Outages covering ~24 months ahead. "
            "Entries may be tentative and not AESO-approved (approval_status=tentative). "
            "Do not confuse with get_approved_transmission_outages."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_long_range_transmission_outages(
        request: LongRangeTransmissionOutagesRequest,
    ) -> TransmissionOutagesResponse:
        return await container.transmission.get_long_range_transmission_outages(request)

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
