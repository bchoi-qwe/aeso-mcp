# SPDX-License-Identifier: MIT
"""Typed MCP adapters for named AESO public operational reports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.reports import (
    DdsMarketReportRequest,
    DdsMarketReportResponse,
    FfrNetScheduleRequest,
    FfrNetScheduleResponse,
    SupplyAdequacyRequest,
    SupplyAdequacyResponse,
    SupplySurplusRequest,
    SupplySurplusResponse,
    SystemEventsRequest,
    SystemEventsResponse,
    TmrReferencePriceRequest,
    TmrReferencePriceResponse,
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


def register_report_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register bounded, source-specific operational-report tools."""

    @mcp.tool(
        name="get_supply_adequacy",
        description=(
            "Returns the official AESO hourly Supply Adequacy and Market Supply Cushion "
            "status bands. These published categorical metrics are distinct from the "
            "derived assess_supply_tightness screening label."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_supply_adequacy(request: SupplyAdequacyRequest) -> SupplyAdequacyResponse:
        return await container.reports.get_supply_adequacy(request)

    @mcp.tool(
        name="get_supply_surplus",
        description=(
            "Returns official AESO hourly Supply Surplus status observations. Zero-price "
            "states are forecasts/statuses; they are not inferred from observed Pool Price."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_supply_surplus(request: SupplySurplusRequest) -> SupplySurplusResponse:
        return await container.reports.get_supply_surplus(request)

    @mcp.tool(
        name="get_ffr_net_schedule",
        description=(
            "Returns the bounded official FFR Net Schedule archive. AESO defines scheduled "
            "imports as negative and exports as positive; this is not FFR offered or dispatched."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_ffr_net_schedule(request: FfrNetScheduleRequest) -> FfrNetScheduleResponse:
        return await container.reports.get_ffr_net_schedule(request)

    @mcp.tool(
        name="get_dispatch_down_service",
        description=(
            "Returns bounded official AESO Dispatch Down Service availability publications. "
            "DDS is distinct from energy-market dispatch and metered generation."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_dispatch_down_service(
        request: DdsMarketReportRequest,
    ) -> DdsMarketReportResponse:
        return await container.reports.get_dds_market_report(request)

    @mcp.tool(
        name="get_tmr_reference_price",
        description=(
            "Returns the official AESO Transmission Must-Run reference price by effective date; "
            "the published value is returned without independent recomputation."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_tmr_reference_price(
        request: TmrReferencePriceRequest,
    ) -> TmrReferencePriceResponse:
        return await container.reports.get_tmr_reference_price(request)

    @mcp.tool(
        name="get_system_events",
        description=(
            "Returns bounded official AIES Event Log messages with a deterministic descriptive "
            "classification. Raw published comments remain authoritative and no event end is inferred."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_system_events(request: SystemEventsRequest) -> SystemEventsResponse:
        return await container.reports.get_system_events(request)


__all__ = ["register_report_tools"]
