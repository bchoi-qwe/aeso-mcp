# SPDX-License-Identifier: MIT
"""MCP adapters for the official AESO research-data archive and analyses."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.research_data import (
    CongestionAnalysisRequest,
    CongestionAnalysisResponse,
    FrequencyAnalysisRequest,
    FrequencyAnalysisResponse,
    ParticipantConcentrationRequest,
    ParticipantConcentrationResponse,
    RegionalAnalysisRequest,
    RegionalAnalysisResponse,
    ResearchDataRequest,
    ResearchDataResponse,
    ScarcityAnalysisRequest,
    ScarcityAnalysisResponse,
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


def register_research_data_tools(mcp: FastMCP, container: AppContainer) -> None:
    """Register one semantic archive read plus bounded intent-level analyses."""
    research_data = cast(Any, container).research_data

    @mcp.tool(
        name="get_research_data",
        description=(
            "Returns a bounded, paginated page from one verified official AESO research archive: "
            "historical adequacy/cushion web codes, transmission outages, planning-area hourly "
            "load/generation, constrained volume, EEA events, operating-reserve directives, or "
            "the current Pool Participant registry. Raw system-frequency rows are intentionally "
            "not exposed; use analyze_system_frequency for a compact six-hour analysis."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def get_research_data(request: ResearchDataRequest) -> ResearchDataResponse:
        return await research_data.get_research_data(request)

    @mcp.tool(
        name="analyze_participant_concentration",
        description=(
            "Summarizes historical energy-merit-order offered blocks by current AESO asset and "
            "Pool Participant mappings, including shares and HHI. Unmapped blocks are reported; "
            "the result does not establish historical ownership, market power, or causality."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_participant_concentration(
        request: ParticipantConcentrationRequest,
    ) -> ParticipantConcentrationResponse:
        return await research_data.analyze_participant_concentration(request)

    @mcp.tool(
        name="analyze_regional_load_generation",
        description=(
            "Aggregates official AESO planning-area hourly load and generation by region and "
            "planning area, preserving source MW/MWh semantics and reporting only observed rows."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_regional_load_generation(
        request: RegionalAnalysisRequest,
    ) -> RegionalAnalysisResponse:
        return await research_data.analyze_regional_load_generation(request)

    @mcp.tool(
        name="analyze_constrained_volume",
        description=(
            "Summarizes official AESO constrained volume by planning area and fuel type, with an "
            "optional exact-UTC Pool Price join. This is descriptive association, not causal "
            "congestion attribution."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_constrained_volume(
        request: CongestionAnalysisRequest,
    ) -> CongestionAnalysisResponse:
        return await research_data.analyze_constrained_volume(request)

    @mcp.tool(
        name="analyze_scarcity",
        description=(
            "Counts official AESO Supply Adequacy and Supply Cushion web codes and EEA levels, "
            "optionally adding Pool Price threshold context. Categorical web codes are not "
            "converted to numeric reserve values and no cause is inferred."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_scarcity(request: ScarcityAnalysisRequest) -> ScarcityAnalysisResponse:
        return await research_data.analyze_scarcity(request)

    @mcp.tool(
        name="analyze_system_frequency",
        description=(
            "Computes compact statistics from AESO's verified 10-second frequency archive for a "
            "maximum six elapsed-hour window. Raw yearly frequency files and raw rows are not "
            "returned. Flagged threshold exposure is a 10-second interval proxy/upper bound, "
            "not exact time outside the threshold."
        ),
        annotations=_READ_ONLY,
    )
    @map_errors
    async def analyze_system_frequency(
        request: FrequencyAnalysisRequest,
    ) -> FrequencyAnalysisResponse:
        return await research_data.analyze_system_frequency(request)


__all__ = ["register_research_data_tools"]
