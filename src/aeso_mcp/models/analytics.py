# SPDX-License-Identifier: MIT
"""Analytical domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class AssetEnergyRevenueRequest(DateRangeRequest):
    """Join hourly metered energy to hourly Pool Price for selected assets."""

    asset_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("asset_ids")
    @classmethod
    def _normalize_asset_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values if value.strip()]
        if not normalized:
            raise ValueError("Provide at least one asset ID.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("asset_ids must be unique.")
        if any("," in value for value in normalized):
            raise ValueError("asset_ids must contain individual asset IDs.")
        return normalized


class AssetEnergyRevenueResult(BaseModel):
    """Gross energy-market revenue for one selected asset."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    matched_mwh: float
    gross_energy_revenue_cad: float
    realized_price_cad_per_mwh: float | None = None
    average_market_price_cad_per_mwh: float | None = None
    capture_rate: float | None = None
    matched_observations: int
    missing_intervals: int
    missing_metered_intervals: int
    missing_price_intervals: int


class AssetEnergyRevenueResponse(WarningMixin):
    """Deterministic gross Pool Price revenue from metered-energy joins."""

    results: list[AssetEnergyRevenueResult]
    metadata: DatasetMetadata


class CsdMeteredComparisonRequest(DateRangeRequest):
    """Compare hourly operational CSD generation with hourly metered energy."""

    asset_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("asset_ids")
    @classmethod
    def _normalize_csd_asset_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values if value.strip()]
        if not normalized:
            raise ValueError("Provide at least one asset ID.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("asset_ids must be unique.")
        if any("," in value for value in normalized):
            raise ValueError("asset_ids must contain individual asset IDs.")
        return normalized


class CsdMeteredComparisonResult(BaseModel):
    """Hourly CSD-versus-metered comparison for one asset."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    matched_hours: int
    operational_generation_estimate_mwh: float
    metered_energy_mwh: float
    mean_signed_difference_mwh: float | None = None
    mean_absolute_difference_mwh: float | None = None
    maximum_absolute_difference_mwh: float | None = None
    mean_absolute_percentage_difference: float | None = None
    missing_csd_hours: int
    missing_metered_hours: int


class CsdMeteredComparisonResponse(WarningMixin):
    """Operational CSD generation and settlement-metered energy are separate concepts."""

    results: list[CsdMeteredComparisonResult]
    metadata: DatasetMetadata


# Acronym-style aliases are kept for callers that use the source's CSD spelling.
CSDMeteredComparisonRequest = CsdMeteredComparisonRequest
CSDMeteredComparisonResult = CsdMeteredComparisonResult
CSDMeteredComparisonResponse = CsdMeteredComparisonResponse


RampSeries = Literal["ail", "net_load", "wind", "solar", "asset"]
RampCadence = Literal["hourly", "5-minute"]


class RampAnalysisRequest(DateRangeRequest):
    """Request cadence-aware ramp statistics for one supported market series."""

    series: RampSeries = "ail"
    cadence: RampCadence = "hourly"
    asset_ids: list[str] = Field(default_factory=list, max_length=20)
    percentiles: list[float] = Field(default_factory=lambda: [5, 25, 50, 75, 95], max_length=10)
    largest_interval_count: int = Field(default=10, ge=1, le=50)

    @field_validator("asset_ids")
    @classmethod
    def _normalize_ramp_asset_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("asset_ids must be unique.")
        if any("," in value for value in normalized):
            raise ValueError("asset_ids must contain individual asset IDs.")
        return normalized

    @field_validator("percentiles")
    @classmethod
    def _valid_ramp_percentiles(cls, values: list[float]) -> list[float]:
        if any(value < 0 or value > 100 for value in values):
            raise ValueError("percentiles must be between 0 and 100.")
        if len(values) != len(set(values)):
            raise ValueError("percentiles must be unique.")
        return sorted(values)

    @model_validator(mode="after")
    def _asset_filter_is_explicit(self) -> RampAnalysisRequest:
        if self.series == "asset" and not self.asset_ids:
            raise ValueError("asset_ids are required when series='asset'.")
        if self.series != "asset" and self.asset_ids:
            raise ValueError("asset_ids are only valid when series='asset'.")
        return self


class RampInterval(BaseModel):
    """One consecutive, cadence-validated ramp observation."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    from_value: float
    to_value: float
    delta_mw: float
    ramp_rate_mw_per_hour: float
    direction: Literal["up", "down", "flat"]


class RampAnalysisResponse(WarningMixin):
    """Ramp statistics with explicit requested and observed cadence."""

    series: RampSeries
    cadence: RampCadence
    cadence_minutes: int
    observation_count: int
    ramp_observation_count: int
    maximum_up_ramp_mw: float | None = None
    maximum_down_ramp_mw: float | None = None
    maximum_up_ramp_mw_per_hour: float | None = None
    maximum_down_ramp_mw_per_hour: float | None = None
    ramp_percentiles_mw: dict[str, float] = Field(default_factory=dict)
    largest_ramps: list[RampInterval] = Field(default_factory=list)
    metadata: DatasetMetadata


class SupplySurplusAnalysisRequest(DateRangeRequest):
    """Bounded descriptive analysis of officially published surplus events."""


class SupplySurplusEventMarketEvidence(BaseModel):
    """Observed market values aligned to one published supply-surplus event."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime | None = None
    duration_hours: float | None = None
    status: str
    average_pool_price_cad_per_mwh: float | None = None
    minimum_pool_price_cad_per_mwh: float | None = None
    maximum_pool_price_cad_per_mwh: float | None = None
    average_load_mw: float | None = None
    average_renewable_generation_mw: float | None = None
    price_observation_count: int = 0
    load_observation_count: int = 0
    renewable_observation_count: int = 0


class SupplySurplusAnalysisResponse(WarningMixin):
    """Descriptive associations around AESO-published supply-surplus states."""

    event_count: int
    explicitly_bounded_event_count: int
    total_explicit_duration_hours: float
    events: list[SupplySurplusEventMarketEvidence]
    metadata: DatasetMetadata


ForecastAnalyticsSeries = Literal["ail", "pool_price", "wind", "solar", "wind_solar"]


class ForecastErrorAnalyticsRequest(DateRangeRequest):
    """Generic forecast-error request for supported actual/forecast pairs."""

    series: ForecastAnalyticsSeries = "ail"
    percentiles: list[float] = Field(default_factory=lambda: [50, 90, 95], max_length=10)

    @field_validator("percentiles")
    @classmethod
    def _valid_forecast_percentiles(cls, values: list[float]) -> list[float]:
        if any(value < 0 or value > 100 for value in values):
            raise ValueError("percentiles must be between 0 and 100.")
        if len(values) != len(set(values)):
            raise ValueError("percentiles must be unique.")
        return sorted(values)


class ForecastErrorAnalyticsInterval(BaseModel):
    """One paired forecast and actual value with source-agnostic units."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime | None = None
    actual_value: float
    forecast_value: float
    error: float
    absolute_error: float
    absolute_percentage_error: float | None = None
    unit: str
    lead_time_hours: float | None = None


class ForecastErrorAnalyticsByHour(BaseModel):
    """Forecast-error summary grouped by local market hour."""

    model_config = ConfigDict(extra="forbid")

    market_hour: int
    observation_count: int
    mean_error: float
    mean_absolute_error: float
    root_mean_squared_error: float


class ForecastErrorAnalyticsByLeadTime(BaseModel):
    """Forecast-error summary grouped by published lead time when available."""

    model_config = ConfigDict(extra="forbid")

    lead_time_hours: float
    observation_count: int
    mean_error: float
    mean_absolute_error: float
    root_mean_squared_error: float


class ForecastErrorAnalyticsResponse(WarningMixin):
    """Generalized forecast-error metrics with explicit missing-pair counts."""

    series: ForecastAnalyticsSeries
    observation_count: int
    mean_error: float | None = None
    mean_absolute_error: float | None = None
    root_mean_squared_error: float | None = None
    mean_absolute_percentage_error: float | None = None
    error_percentiles: dict[str, float] = Field(default_factory=dict)
    missing_forecast_count: int = 0
    missing_actual_count: int = 0
    by_market_hour: list[ForecastErrorAnalyticsByHour] = Field(default_factory=list)
    by_lead_time: list[ForecastErrorAnalyticsByLeadTime] = Field(default_factory=list)
    intervals: list[ForecastErrorAnalyticsInterval] = Field(default_factory=list)
    metadata: DatasetMetadata
