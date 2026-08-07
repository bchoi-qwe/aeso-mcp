# SPDX-License-Identifier: MIT
"""Analytics MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.analytics import (
    CompareForecastToActualRequest,
    CompareForecastToActualResponse,
    CompareMarketPeriodsRequest,
    CompareMarketPeriodsResponse,
    ExplainMarketConditionsRequest,
    ExplainMarketConditionsResponse,
    FindPriceEventsRequest,
    FindPriceEventsResponse,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from aeso_mcp.app import AppContainer


def register_analytics_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register deterministic analytics tools."""

    @mcp.tool(
        name="compare_market_periods",
        description=(
            "Compares aggregate pool-price and load statistics between two America/Edmonton "
            "market periods. Returns averages, min/max/median prices, load stats, and deltas. "
            "Does not assert causation."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def compare_market_periods(
        request: CompareMarketPeriodsRequest,
    ) -> CompareMarketPeriodsResponse:
        return await container.analytics.compare_market_periods(request)

    @mcp.tool(
        name="find_price_events",
        description=(
            "Detects sustained high Pool Price events in CAD/MWh over [start, end). "
            "Threshold may be an absolute CAD/MWh value or a percentile (default 90th). "
            "Returns event boundaries, duration, peak/average price, and load context when available."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def find_price_events(request: FindPriceEventsRequest) -> FindPriceEventsResponse:
        return await container.analytics.find_price_events(request)

    @mcp.tool(
        name="explain_market_conditions",
        description=(
            "Returns structured evidence for market conditions in a focus window versus a "
            "baseline window (default: immediately preceding equal-length window). Includes "
            "observed metrics and associated_changes. Does not claim causation; the calling "
            "model should produce any natural-language explanation."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def explain_market_conditions(
        request: ExplainMarketConditionsRequest,
    ) -> ExplainMarketConditionsResponse:
        return await container.analytics.explain_market_conditions(request)

    @mcp.tool(
        name="compare_forecast_to_actual",
        description=(
            "Compares Alberta Internal Load forecast versus actual over [start, end) in MW. "
            "Returns mean error, MAE, RMSE, MAPE, and paired intervals. "
            "Timestamps are America/Edmonton."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def compare_forecast_to_actual(
        request: CompareForecastToActualRequest,
    ) -> CompareForecastToActualResponse:
        return await container.analytics.compare_forecast_to_actual(request)
