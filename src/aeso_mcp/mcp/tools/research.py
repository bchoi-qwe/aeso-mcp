# SPDX-License-Identifier: MIT
"""Market-research MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.analytics import (
    ForecastErrorAnalyticsRequest,
    ForecastErrorAnalyticsResponse,
)
from aeso_mcp.models.research import (
    AssetDispatchRequest,
    AssetDispatchResponse,
    CapturePriceRequest,
    CapturePriceResponse,
    GenerationAnalysisRequest,
    GenerationMixResponse,
    IntertieUtilizationRequest,
    IntertieUtilizationResponse,
    MarketEventRequest,
    MarketEventResponse,
    NetLoadRequest,
    NetLoadResponse,
    OutageImpactRequest,
    OutageImpactResponse,
    PriceDurationCurveRequest,
    PriceDurationCurveResponse,
    PriceStatisticsRequest,
    PriceStatisticsResponse,
    SupplyStackRequest,
    SupplyStackResponse,
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


def register_research_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register deterministic calculations that consume complete source series."""

    @mcp.tool(
        name="get_price_statistics",
        description=(
            "Calculates count, mean, median, range, population standard deviation, negative/high "
            "price hours, and requested percentiles from complete hourly Pool Price observations."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_price_statistics(
        request: PriceStatisticsRequest,
    ) -> PriceStatisticsResponse:
        return await container.research.get_price_statistics(request)

    @mcp.tool(
        name="get_price_duration_curve",
        description=(
            "Returns an evenly sampled Pool Price duration curve sorted from highest to lowest "
            "with exceedance percentages over [start, end)."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_price_duration_curve(
        request: PriceDurationCurveRequest,
    ) -> PriceDurationCurveResponse:
        return await container.research.get_price_duration_curve(request)

    @mcp.tool(
        name="analyze_market_event",
        description=(
            "Compares structured price, demand/forecast, supply, merit-order, intertie, "
            "commitment, and operating-reserve evidence in a focus window against a supplied or "
            "immediately preceding baseline. Returns descriptive associations, not causal claims."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_market_event(request: MarketEventRequest) -> MarketEventResponse:
        return await container.research.analyze_market_event(request)

    @mcp.tool(
        name="calculate_capture_prices",
        description=(
            "Joins hourly Pool Price to official CSD generation and calculates generation-weighted "
            "capture price and capture rate by requested asset and/or fuel group."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def calculate_capture_prices(request: CapturePriceRequest) -> CapturePriceResponse:
        return await container.research.calculate_capture_prices(request)

    @mcp.tool(
        name="analyze_net_load",
        description=(
            "Calculates hourly Alberta Internal Load minus selected CSD renewable generation "
            "(wind and solar by default), including average, peak, and minimum net load."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_net_load(request: NetLoadRequest) -> NetLoadResponse:
        return await container.research.analyze_net_load(request)

    @mcp.tool(
        name="analyze_supply_stack",
        description=(
            "Analyzes one historical Energy Merit Order hour: price-sorted offer blocks, offered "
            "and dispatched MW, and the highest dispatched offer price."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_supply_stack(request: SupplyStackRequest) -> SupplyStackResponse:
        return await container.research.analyze_supply_stack(request)

    @mcp.tool(
        name="analyze_intertie_utilization",
        description=(
            "Summarizes gross offers relative to available intertie transfer capability by path "
            "and direction. This is an offer-to-capability proxy, not metered flow."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_intertie_utilization(
        request: IntertieUtilizationRequest,
    ) -> IntertieUtilizationResponse:
        return await container.research.analyze_intertie_utilization(request)

    @mcp.tool(
        name="analyze_generation_mix",
        description=(
            "Aggregates official hourly individual-asset CSD generation into energy, share, "
            "average MW, and peak MW by fuel type."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_generation_mix(
        request: GenerationAnalysisRequest,
    ) -> GenerationMixResponse:
        return await container.research.analyze_generation_mix(request)

    @mcp.tool(
        name="analyze_asset_dispatch",
        description=(
            "Summarizes official hourly CSD dispatch for selected assets: energy, average/peak "
            "generation, available capacity factor, and hourly ramp extremes."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_asset_dispatch(request: AssetDispatchRequest) -> AssetDispatchResponse:
        return await container.research.analyze_asset_dispatch(request)

    @mcp.tool(
        name="analyze_outage_impact",
        description=(
            "Joins hourly generator outage capacity to Pool Price and reports high-outage versus "
            "other-hour price differences plus Pearson correlation as non-causal associations."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_outage_impact(request: OutageImpactRequest) -> OutageImpactResponse:
        return await container.research.analyze_outage_impact(request)

    @mcp.tool(
        name="analyze_forecast_error",
        description=(
            "Calculates forecast-minus-actual bias, MAE, RMSE, denominator-aware MAPE, error "
            "percentiles, and breakdowns by market hour and lead time for AIL, Pool Price, "
            "wind, solar, or combined wind/solar when the official source provides paired values."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_forecast_error(
        request: ForecastErrorAnalyticsRequest,
    ) -> ForecastErrorAnalyticsResponse:
        return await container.analytics.analyze_forecast_error(request)
