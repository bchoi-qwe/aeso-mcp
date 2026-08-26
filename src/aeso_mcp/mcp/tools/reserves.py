# SPDX-License-Identifier: MIT
"""Operating-reserve MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.reserves import (
    OperatingReserveActivationsResponse,
    OperatingReserveDateRangeRequest,
    OperatingReserveForecastRequest,
    OperatingReserveForecastResponse,
    OperatingReservePricesResponse,
    OperatingReserveSummaryRequest,
    OperatingReserveSummaryResponse,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from aeso_mcp.app import AppContainer

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def register_reserve_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register raw and summarized operating-reserve reports."""

    @mcp.tool(
        name="get_operating_reserve_prices",
        description=(
            "Returns daily active and standby operating-reserve price components and volumes by "
            "regulating, spinning, or supplemental product and time block. Active price, standby "
            "premium, activation strike, and clearing blended price remain distinct."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_operating_reserve_prices(
        request: OperatingReserveDateRangeRequest,
    ) -> OperatingReservePricesResponse:
        return await container.reserves.get_prices(request)

    @mcp.tool(
        name="get_operating_reserve_forecast",
        description=(
            "Returns the current public seven-day hourly forecast of active and standby "
            "regulating, spinning, and supplemental operating-reserve volumes in MW."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_operating_reserve_forecast(
        request: OperatingReserveForecastRequest,
    ) -> OperatingReserveForecastResponse:
        return await container.reserves.get_forecast(request)

    @mcp.tool(
        name="get_operating_reserve_activations",
        description=(
            "Returns hourly standby operating-reserve activation volume and volume-weighted "
            "activation price for an inclusive market-date range."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_operating_reserve_activations(
        request: OperatingReserveDateRangeRequest,
    ) -> OperatingReserveActivationsResponse:
        return await container.reserves.get_activations(request)

    @mcp.tool(
        name="summarize_operating_reserve_market",
        description=(
            "Summarizes active prices or standby clearing blended prices and volumes by reserve "
            "product; optionally joins standby activations using volume-weighted activation price."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def summarize_operating_reserve_market(
        request: OperatingReserveSummaryRequest,
    ) -> OperatingReserveSummaryResponse:
        return await container.reserves.summarize(request)
