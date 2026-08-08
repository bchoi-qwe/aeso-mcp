# SPDX-License-Identifier: MIT
"""MCP tools for market-power mitigation public reports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.market_power import (
    MarketPowerMitigationRequest,
    McsinrResponse,
    SecondaryOfferPriceLimitResponse,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from aeso_mcp.app import AppContainer


def register_market_power_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register MCSINR and Secondary Offer Price Limit tools."""

    @mcp.tool(
        name="get_monthly_cumulative_net_revenue",
        description=(
            "Returns the current AESO Monthly Cumulative Settlement Interval Net Revenue "
            "(MCSINR) public report. Includes cumulative CAD vs 1/6 annualized unavoidable "
            "costs and whether the secondary offer price limit trigger has been reached. "
            "Timestamps: America/Edmonton hour-ending intervals."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_monthly_cumulative_net_revenue(
        request: MarketPowerMitigationRequest,
    ) -> McsinrResponse:
        return await container.market_power.get_monthly_cumulative_net_revenue(request)

    @mcp.tool(
        name="get_secondary_offer_price_limit",
        description=(
            "Returns the current AESO Secondary Offer Price Limit public report: whether the "
            "secondary offer cap is in effect and the CAD/MWh limit when posted. A null limit "
            "means the cap is not in effect."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_secondary_offer_price_limit(
        request: MarketPowerMitigationRequest,
    ) -> SecondaryOfferPriceLimitResponse:
        return await container.market_power.get_secondary_offer_price_limit(request)
