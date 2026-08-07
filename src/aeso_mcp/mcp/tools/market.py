# SPDX-License-Identifier: MIT
"""Market MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.generation import (
    GenerationRequest,
    GenerationResponse,
    LoadRequest,
    LoadResponse,
)
from aeso_mcp.models.grid import MarketSnapshotResponse
from aeso_mcp.models.prices import (
    PoolPriceRequest,
    PoolPriceResponse,
    SystemMarginalPriceRequest,
    SystemMarginalPriceResponse,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from aeso_mcp.app import AppContainer


def register_market_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register core market retrieval tools."""

    @mcp.tool(
        name="get_market_snapshot",
        description=(
            "Returns a cohesive current-state view of the Alberta electricity market including "
            "recent pool price, system marginal price, Alberta Internal Load, generation by fuel, "
            "net interchange, and operating reserves. Units: prices CAD/MWh, power MW. "
            "Timezone: America/Edmonton."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_market_snapshot() -> MarketSnapshotResponse:
        return await container.market.get_market_snapshot()

    @mcp.tool(
        name="get_pool_prices",
        description=(
            "Returns actual AESO hourly Pool Price observations in CAD/MWh for the requested "
            "market interval [start, end). Use get_system_marginal_prices for minute-level "
            "real-time pricing. Timestamps are America/Edmonton. Maximum range: 366 days."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_pool_prices(request: PoolPriceRequest) -> PoolPriceResponse:
        return await container.market.get_pool_prices(request)

    @mcp.tool(
        name="get_system_marginal_prices",
        description=(
            "Returns AESO System Marginal Price (SMP) observations in CAD/MWh with minute-level "
            "interval boundaries for [start, end). Prefer get_pool_prices for hourly settlement "
            "prices. Timestamps are America/Edmonton. Maximum range: 7 days."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_system_marginal_prices(
        request: SystemMarginalPriceRequest,
    ) -> SystemMarginalPriceResponse:
        return await container.market.get_system_marginal_prices(request)

    @mcp.tool(
        name="get_load",
        description=(
            "Returns Alberta Internal Load (AIL) observations in MW for [start, end). "
            "Optionally includes load forecast values when available. "
            "Timestamps are America/Edmonton. Maximum range: 90 days."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_load(request: LoadRequest) -> LoadResponse:
        return await container.market.get_load(request)

    @mcp.tool(
        name="get_generation",
        description=(
            "Returns Alberta generation data. Omit start/end for the current fuel-mix snapshot "
            "(all fuels, MW). Provide start and end for historical wind and solar hourly "
            "generation. Renewable share uses wind + solar + hydro over total generation."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_generation(request: GenerationRequest) -> GenerationResponse:
        return await container.market.get_generation(request)
