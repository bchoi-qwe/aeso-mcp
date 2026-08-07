# SPDX-License-Identifier: MIT
"""Load and generation domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aeso_mcp.models.common import DatasetMetadata, DateRangeRequest, WarningMixin


class LoadRequest(DateRangeRequest):
    """Request Alberta Internal Load (AIL) observations."""

    include_forecast: bool = Field(
        default=False,
        description="When true, include AESO load forecast values when available.",
    )


class LoadInterval(BaseModel):
    """One load observation interval."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime | None = None
    load_mw: float
    load_forecast_mw: float | None = None


class LoadResponse(WarningMixin):
    """Alberta Internal Load observations in MW."""

    intervals: list[LoadInterval]
    metadata: DatasetMetadata


class GenerationRequest(BaseModel):
    """Request generation / fuel-mix data.

    Without a date range, returns the current CSD fuel mix (all fuels).
    With a date range, returns historical wind and solar generation where available.
    """

    model_config = ConfigDict(extra="forbid")

    start: datetime | None = None
    end: datetime | None = None


class FuelMixComponent(BaseModel):
    """Net generation for a single fuel type."""

    model_config = ConfigDict(extra="forbid")

    fuel_type: str
    generation_mw: float
    maximum_capability_mw: float | None = None


class GenerationSnapshot(BaseModel):
    """Current (or single-interval) generation by fuel."""

    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    components: list[FuelMixComponent]
    total_generation_mw: float
    renewable_generation_mw: float
    renewable_share: float = Field(
        description="Share of total generation from wind + solar + hydro (0-1).",
    )


class GenerationInterval(BaseModel):
    """Historical generation interval (typically wind/solar)."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime | None = None
    fuel_type: str
    generation_mw: float


class GenerationResponse(WarningMixin):
    """Generation / fuel-mix response."""

    snapshot: GenerationSnapshot | None = None
    intervals: list[GenerationInterval] = Field(default_factory=list)
    metadata: DatasetMetadata
