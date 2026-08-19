# SPDX-License-Identifier: MIT
"""Operational-report and compact historical-analysis MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.operations import (
    DailyPageRequest,
    DateRangePageRequest,
    EnergyMeritOrderResponse,
    GenerationCapacityResponse,
    IntertieCapabilityRequest,
    IntertieCapabilityResponse,
    IntertieOutagesResponse,
    LoadOutageForecastResponse,
    MarketHistorySummaryRequest,
    MarketHistorySummaryResponse,
    MeteredVolumeRequest,
    MeteredVolumeResponse,
    OperatingReserveOfferControlResponse,
    SupplyTightnessResponse,
    UnitCommitmentResponse,
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


def register_operations_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register authenticated operational reports and compact derived analytics."""

    @mcp.tool(
        name="get_energy_merit_order",
        description=(
            "Returns one historical AESO Energy Merit Order report date as paginated offer "
            "blocks. Reports are released with a 60-day delay. Prices are CAD/MWh and volumes "
            "are MW; timestamps use America/Edmonton."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_energy_merit_order(
        request: DailyPageRequest,
    ) -> EnergyMeritOrderResponse:
        return await container.operations.get_energy_merit_order(request)

    @mcp.tool(
        name="get_unit_commitments",
        description=(
            "Returns AESO generating-unit commitment directives for an inclusive date range, "
            "paginated and bounded to 31 days. Includes issue, begin, operation-start, and "
            "operation-end timestamps in America/Edmonton."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_unit_commitments(
        request: DateRangePageRequest,
    ) -> UnitCommitmentResponse:
        return await container.operations.get_unit_commitments(request)

    @mcp.tool(
        name="get_generation_capacity",
        description=(
            "Returns hourly AIES maximum and available generation capability plus operating and "
            "mothball outages by fuel class. The inclusive date range is bounded to 31 days; "
            "values are MW and output is paginated."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_generation_capacity(
        request: DateRangePageRequest,
    ) -> GenerationCapacityResponse:
        return await container.operations.get_generation_capacity(request)

    @mcp.tool(
        name="get_load_outage_forecast",
        description=(
            "Returns AESO hourly load-outage forecast observations in MW for an inclusive, "
            "maximum 31-day range. Timestamps use America/Edmonton and output is paginated."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_load_outage_forecast(
        request: DateRangePageRequest,
    ) -> LoadOutageForecastResponse:
        return await container.operations.get_load_outage_forecast(request)

    @mcp.tool(
        name="get_intertie_capability",
        description=(
            "Returns hourly import/export ATC, TTC, reliability margins, and gross offers for "
            "AESO interties and flowgates. Supports hour-ending bounds and optional versioned "
            "reports; date ranges are limited to 100 days and output is paginated."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_intertie_capability(
        request: IntertieCapabilityRequest,
    ) -> IntertieCapabilityResponse:
        return await container.operations.get_intertie_capability(request)

    @mcp.tool(
        name="get_intertie_outages",
        description=(
            "Returns outages affecting AESO interties or flowgates for an inclusive date range. "
            "Includes affected paths and event boundaries in America/Edmonton; output is paginated."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_intertie_outages(
        request: DateRangePageRequest,
    ) -> IntertieOutagesResponse:
        return await container.operations.get_intertie_outages(request)

    @mcp.tool(
        name="get_metered_volumes",
        description=(
            "Returns hourly AESO metered energy in MWh by asset. Filter by up to 20 asset IDs or "
            "20 pool-participant IDs (not both); unfiltered requests are limited to 16 days and "
            "all output is paginated."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_metered_volumes(
        request: MeteredVolumeRequest,
    ) -> MeteredVolumeResponse:
        return await container.operations.get_metered_volumes(request)

    @mcp.tool(
        name="get_operating_reserve_offer_control",
        description=(
            "Returns one historical AESO Operating Reserve Offer Control report date as "
            "paginated blocks. Reports are released with a 60-day delay; prices are CAD/MWh, "
            "volumes are MW, and timestamps use America/Edmonton."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_operating_reserve_offer_control(
        request: DailyPageRequest,
    ) -> OperatingReserveOfferControlResponse:
        return await container.operations.get_operating_reserve_offer_control(request)

    @mcp.tool(
        name="summarize_market_history",
        description=(
            "Returns compact hourly, daily, weekly, or monthly Pool Price statistics and optional "
            "Alberta Internal Load statistics for [start, end). Use this before requesting raw "
            "series for long periods; summaries are capped at 400 buckets."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def summarize_market_history(
        request: MarketHistorySummaryRequest,
    ) -> MarketHistorySummaryResponse:
        return await container.operations.summarize_market_history(request)

    @mcp.tool(
        name="assess_supply_tightness",
        description=(
            "Combines the current market snapshot with hourly available generation capability "
            "and outages. Returns transparent supply-margin arithmetic plus a deterministic "
            "tight/watch/comfortable screening signal; it is not an AESO declaration or a "
            "causal price explanation."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def assess_supply_tightness() -> SupplyTightnessResponse:
        return await container.operations.assess_supply_tightness()
