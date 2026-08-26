# SPDX-License-Identifier: MIT
"""Deterministic market-research analytics contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aeso_mcp.models.common import DatasetMetadata, DateRangeRequest, WarningMixin
from aeso_mcp.models.operations import EnergyMeritOrderBlock


class PriceStatisticsRequest(DateRangeRequest):
    percentiles: list[float] = Field(default_factory=lambda: [5, 25, 50, 75, 95], max_length=10)

    @field_validator("percentiles")
    @classmethod
    def _valid_percentiles(cls, values: list[float]) -> list[float]:
        if any(value < 0 or value > 100 for value in values):
            raise ValueError("percentiles must be between 0 and 100.")
        if len(values) != len(set(values)):
            raise ValueError("percentiles must be unique.")
        return sorted(values)


class PercentileValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    percentile: float
    value: float


class PriceStatisticsResponse(WarningMixin):
    observation_count: int
    average_cad_per_mwh: float | None = None
    median_cad_per_mwh: float | None = None
    minimum_cad_per_mwh: float | None = None
    maximum_cad_per_mwh: float | None = None
    standard_deviation_cad_per_mwh: float | None = None
    negative_price_hours: int = 0
    hours_at_or_above_100_cad_per_mwh: int = 0
    percentiles: list[PercentileValue]
    metadata: DatasetMetadata


class PriceDurationCurveRequest(DateRangeRequest):
    points: int = Field(default=100, ge=2, le=500)


class PriceDurationPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exceedance_percent: float
    pool_price_cad_per_mwh: float


class PriceDurationCurveResponse(WarningMixin):
    observation_count: int
    points: list[PriceDurationPoint]
    metadata: DatasetMetadata


class MarketEventRequest(DateRangeRequest):
    baseline_start: datetime | None = None
    baseline_end: datetime | None = None

    @model_validator(mode="after")
    def _paired_baseline(self) -> MarketEventRequest:
        if (self.baseline_start is None) ^ (self.baseline_end is None):
            raise ValueError("baseline_start and baseline_end must be provided together.")
        return self


class ResearchMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    focus_value: float | None = None
    baseline_value: float | None = None
    absolute_change: float | None = None
    percent_change: float | None = None
    unit: str


class MarketEventPriceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    average_cad_per_mwh: float | None = None
    maximum_cad_per_mwh: float | None = None
    volatility_cad_per_mwh: float | None = None
    high_price_hours: int = 0


class MarketEventDemandEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    average_load_mw: float | None = None
    maximum_load_mw: float | None = None
    maximum_up_ramp_mw: float | None = None
    forecast_mae_mw: float | None = None


class MarketEventSupplyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    average_available_capability_mw: float | None = None
    average_outage_mw: float | None = None
    generation_mwh_by_fuel: dict[str, float] = Field(default_factory=dict)
    renewable_generation_share: float | None = None
    average_net_load_mw: float | None = None


class MarketEventMeritOrderEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_block_count: int = 0
    marginal_dispatched_offer_cad_per_mwh: float | None = None
    offered_volume_mw: float | None = None
    offer_volume_hhi: float | None = None


class MarketEventIntertieEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    average_available_capability_mw: float | None = None
    average_gross_offer_mw: float | None = None
    average_offer_to_capability_ratio: float | None = None
    average_import_available_capability_mw: float | None = None
    average_export_available_capability_mw: float | None = None
    average_import_gross_offer_mw: float | None = None
    average_export_gross_offer_mw: float | None = None
    capability_outage_count: int = 0


class MarketEventReserveEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    average_active_price_cad_per_mw: float | None = None
    average_standby_clearing_price_cad_per_mw: float | None = None
    average_active_volume_mw: float | None = None
    average_standby_volume_mw: float | None = None
    standby_activated_volume_mw: float | None = None
    offer_control_block_count: int = 0


class MarketEventEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    price: MarketEventPriceEvidence
    demand: MarketEventDemandEvidence
    supply: MarketEventSupplyEvidence
    merit_order: MarketEventMeritOrderEvidence
    interties: MarketEventIntertieEvidence
    reserves: MarketEventReserveEvidence
    commitment_count: int | None = None


class MarketEventResponse(WarningMixin):
    focus_start: datetime
    focus_end: datetime
    baseline_start: datetime
    baseline_end: datetime
    evidence: MarketEventEvidence
    metrics: list[ResearchMetric]
    ranked_associations: list[str]
    methodology: str
    metadata: DatasetMetadata


class CapturePriceRequest(DateRangeRequest):
    asset_ids: list[str] = Field(default_factory=list, max_length=25)
    fuel_types: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _has_group(self) -> CapturePriceRequest:
        if not self.asset_ids and not self.fuel_types:
            raise ValueError("Provide at least one asset_id or fuel_type.")
        return self


class CapturePriceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: str
    generation_mwh: float
    capture_price_cad_per_mwh: float | None = None
    market_average_price_cad_per_mwh: float | None = None
    capture_rate: float | None = None
    matched_intervals: int


class CapturePriceResponse(WarningMixin):
    results: list[CapturePriceResult]
    metadata: DatasetMetadata


class NetLoadRequest(DateRangeRequest):
    renewable_fuels: list[str] = Field(default_factory=lambda: ["WIND", "SOLAR"], max_length=10)


class NetLoadInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_start: datetime
    load_mw: float
    renewable_generation_mw: float
    net_load_mw: float


class NetLoadResponse(WarningMixin):
    intervals: list[NetLoadInterval]
    average_net_load_mw: float | None = None
    peak_net_load_mw: float | None = None
    minimum_net_load_mw: float | None = None
    metadata: DatasetMetadata


class SupplyStackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_date: date
    hour_ending: int = Field(ge=1, le=24)
    limit: int = Field(default=500, ge=1, le=2_000)


class SupplyStackResponse(WarningMixin):
    report_date: date
    hour_ending: int
    blocks: list[EnergyMeritOrderBlock]
    offered_mw: float
    dispatched_mw: float
    marginal_offer_cad_per_mwh: float | None = None
    metadata: DatasetMetadata


class IntertieUtilizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    interties: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _ordered(self) -> IntertieUtilizationRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date.")
        return self


class IntertieUtilizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intertie: str
    direction: Literal["import", "export"]
    observation_count: int
    average_available_transfer_capability_mw: float | None = None
    average_gross_offer_mw: float | None = None
    average_offer_to_capability_ratio: float | None = None
    maximum_offer_to_capability_ratio: float | None = None
    constrained_hours: int = 0


class IntertieUtilizationResponse(WarningMixin):
    results: list[IntertieUtilizationResult]
    methodology: str
    metadata: DatasetMetadata


class GenerationAnalysisRequest(DateRangeRequest):
    fuel_types: list[str] = Field(default_factory=list, max_length=20)


class GenerationMixResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fuel_type: str
    generation_mwh: float
    share: float | None = None
    average_generation_mw: float | None = None
    peak_generation_mw: float | None = None


class GenerationMixResponse(WarningMixin):
    results: list[GenerationMixResult]
    total_generation_mwh: float
    metadata: DatasetMetadata


class AssetDispatchRequest(DateRangeRequest):
    asset_ids: list[str] = Field(min_length=1, max_length=25)


class AssetDispatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    fuel_type: str | None = None
    observation_count: int
    generation_mwh: float
    average_generation_mw: float | None = None
    peak_generation_mw: float | None = None
    average_capacity_factor: float | None = None
    maximum_up_ramp_mw: float | None = None
    maximum_down_ramp_mw: float | None = None


class AssetDispatchResponse(WarningMixin):
    results: list[AssetDispatchResult]
    metadata: DatasetMetadata


class OutageImpactRequest(DateRangeRequest):
    high_outage_threshold_mw: float | None = Field(default=None, ge=0)


class OutageImpactResponse(WarningMixin):
    matched_observations: int
    high_outage_observations: int
    threshold_mw: float | None = None
    average_price_high_outage_cad_per_mwh: float | None = None
    average_price_other_hours_cad_per_mwh: float | None = None
    price_difference_cad_per_mwh: float | None = None
    outage_price_correlation: float | None = None
    methodology: str
    metadata: DatasetMetadata


ForecastErrorSeries = Literal["ail"]


class ForecastErrorRequest(DateRangeRequest):
    series: ForecastErrorSeries = "ail"


class ForecastErrorByHour(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market_hour: int
    observation_count: int
    mean_error_mw: float
    mean_absolute_error_mw: float
    root_mean_squared_error_mw: float


class ForecastErrorResponse(WarningMixin):
    series: ForecastErrorSeries
    observation_count: int
    mean_error_mw: float | None = None
    mean_absolute_error_mw: float | None = None
    root_mean_squared_error_mw: float | None = None
    mean_absolute_percentage_error: float | None = None
    by_market_hour: list[ForecastErrorByHour]
    metadata: DatasetMetadata
