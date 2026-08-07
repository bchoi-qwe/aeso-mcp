# SPDX-License-Identifier: MIT
"""Analytical domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aeso_mcp.models.common import DatasetMetadata, DateRangeRequest, WarningMixin


class CompareMarketPeriodsRequest(BaseModel):
    """Compare aggregate pool-price and load statistics across two periods."""

    model_config = ConfigDict(extra="forbid")

    period_a_start: datetime
    period_a_end: datetime
    period_b_start: datetime
    period_b_end: datetime


class PeriodStatistics(BaseModel):
    """Aggregate statistics for one market period."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime
    observation_count: int
    avg_pool_price_cad_per_mwh: float | None = None
    min_pool_price_cad_per_mwh: float | None = None
    max_pool_price_cad_per_mwh: float | None = None
    median_pool_price_cad_per_mwh: float | None = None
    avg_load_mw: float | None = None
    min_load_mw: float | None = None
    max_load_mw: float | None = None


class CompareMarketPeriodsResponse(WarningMixin):
    """Side-by-side period comparison with deltas."""

    period_a: PeriodStatistics
    period_b: PeriodStatistics
    price_avg_delta_cad_per_mwh: float | None = None
    price_avg_pct_change: float | None = None
    load_avg_delta_mw: float | None = None
    load_avg_pct_change: float | None = None
    metadata: DatasetMetadata


class FindPriceEventsRequest(DateRangeRequest):
    """Detect sustained high-price intervals in pool price history."""

    threshold_cad_per_mwh: float | None = Field(
        default=None,
        description="Absolute pool-price threshold in CAD/MWh. Defaults to 90th percentile.",
        ge=0,
    )
    percentile: float | None = Field(
        default=None,
        description="Percentile threshold (0-100) used when absolute threshold is omitted.",
        ge=0,
        le=100,
    )
    min_duration_hours: float = Field(
        default=1.0,
        ge=1.0,
        le=168.0,
        description="Minimum consecutive hours above threshold to count as an event.",
    )


class PriceEvent(BaseModel):
    """One detected high-price event with available evidence."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime
    duration_hours: float
    peak_price_cad_per_mwh: float
    average_price_cad_per_mwh: float
    avg_load_mw: float | None = None
    max_load_mw: float | None = None


class FindPriceEventsResponse(WarningMixin):
    """Detected price events for the requested window."""

    threshold_cad_per_mwh: float
    events: list[PriceEvent]
    metadata: DatasetMetadata


class ExplainMarketConditionsRequest(BaseModel):
    """Request structured evidence for market conditions around a time window."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime
    baseline_start: datetime | None = Field(
        default=None,
        description="Optional baseline window start for comparison. Defaults to prior equal-length window.",
    )
    baseline_end: datetime | None = None


class AssociatedChange(BaseModel):
    """A measured change associated with the focus window (not a causal claim)."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    focus_value: float | None = None
    baseline_value: float | None = None
    absolute_change: float | None = None
    pct_change: float | None = None
    unit: str


class ExplainMarketConditionsResponse(WarningMixin):
    """Structured evidence suitable for an LLM explanation.

    This response intentionally avoids causal language. Fields describe observed
    conditions and associated changes only.
    """

    focus_start: datetime
    focus_end: datetime
    baseline_start: datetime
    baseline_end: datetime
    observed_conditions: dict[str, float | None]
    associated_changes: list[AssociatedChange]
    notable_movements: list[str]
    metadata: DatasetMetadata


class CompareForecastToActualRequest(DateRangeRequest):
    """Compare Alberta Internal Load forecast versus actual over a range."""


class ForecastActualInterval(BaseModel):
    """One paired forecast/actual load observation."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime | None = None
    actual_load_mw: float
    forecast_load_mw: float
    error_mw: float
    abs_error_mw: float
    abs_pct_error: float | None = None


class CompareForecastToActualResponse(WarningMixin):
    """Deterministic forecast accuracy statistics for AIL."""

    observation_count: int
    mean_error_mw: float | None = None
    mean_abs_error_mw: float | None = None
    rmse_mw: float | None = None
    mean_abs_pct_error: float | None = None
    max_abs_error_mw: float | None = None
    intervals: list[ForecastActualInterval] = Field(
        default_factory=list,
        description="Paired intervals (may be truncated for large ranges).",
    )
    metadata: DatasetMetadata
