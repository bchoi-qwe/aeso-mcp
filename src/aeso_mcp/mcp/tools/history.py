# SPDX-License-Identifier: MIT
"""Historical research MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Context

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.forecasts import ForecastResponse, OfficialForecastRequest
from aeso_mcp.models.history import (
    HistoricalGenerationRequest,
    HistoricalGenerationResponse,
    HistoricalStoreStatusResponse,
    HistoricalStoreSyncRequest,
    HistoricalStoreSyncResponse,
    UnitCommitmentSettlementRequest,
    UnitCommitmentSettlementResponse,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from aeso_mcp.app import AppContainer


def register_history_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register bounded archive and storage operations."""

    @mcp.tool(
        name="get_historical_generation",
        description=(
            "Retrieves historical AESO CSD generation by individual asset or fuel over "
            "[start, end), at hourly or five-minute resolution. Use for historical wind, "
            "solar, or other generator output and asset-level CSD records. Supports bounded "
            "asset/fuel filters and pagination. This operational archive is not settlement-metered data."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_historical_generation(
        request: HistoricalGenerationRequest,
    ) -> HistoricalGenerationResponse:
        return await container.history.get_historical_generation(request)

    @mcp.tool(
        name="sync_historical_store",
        description=(
            "Persist selected AESO historical generation, Pool Price, and load observations "
            "into the configured local DuckDB/Parquet historical store. Use to save, sync, or "
            "backfill a bounded period in the local history store. Incrementally ingests "
            "and refreshes preliminary intervals; reports source gaps and duplicate records. "
            "Use to sync or backfill a bounded date range. Requires optional analytics dependencies."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def sync_historical_store(
        request: HistoricalStoreSyncRequest,
        ctx: Context,
    ) -> HistoricalStoreSyncResponse:
        await ctx.report_progress(0, 1, "Starting bounded historical-store synchronization")
        response = await container.history.sync_historical_store(request)
        await ctx.report_progress(1, 1, "Historical-store synchronization complete")
        return response

    @mcp.tool(
        name="get_historical_store_status",
        description=(
            "Reports what AESO historical data is currently stored locally in the DuckDB/Parquet "
            "historical store, including dataset coverage, detected cadence gaps, source-file and "
            "partition counts, schema version, and optional storage dependency availability."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    @map_errors
    async def get_historical_store_status() -> HistoricalStoreStatusResponse:
        return await container.history.get_historical_store_status()

    @mcp.tool(
        name="get_forecast",
        description=(
            "Returns typed official AESO actual and forecast observations over [start, end) "
            "for AIL, Pool Price, wind, solar, or combined wind/solar where the requested "
            "source horizon exists. An optional timezone-aware as_of selects the latest "
            "vintage retrieved and published or issued by that instant; unknown chronology is excluded."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_forecast(request: OfficialForecastRequest) -> ForecastResponse:
        return await container.forecasts.get_forecast(request)

    @mcp.tool(
        name="get_uc_settlement_summary",
        description=(
            "Returns the public AESO hourly Unit Commitment settlement amount in CAD and "
            "charged volume in MW for an inclusive market-date range."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    @map_errors
    async def get_uc_settlement_summary(
        request: UnitCommitmentSettlementRequest,
    ) -> UnitCommitmentSettlementResponse:
        return await container.history.get_uc_settlement_summary(request)
