# SPDX-License-Identifier: MIT
"""Operational-market reports exposed by the authenticated AESO APIM catalog."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aeso_mcp.models.common import DatasetMetadata, DateRangeRequest, WarningMixin


class PageRequest(BaseModel):
    """Bounded, offset-based page controls for deterministic report reads."""

    model_config = ConfigDict(extra="forbid")

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)


class PageInfo(BaseModel):
    """Pagination state for a stable in-memory report result."""

    model_config = ConfigDict(extra="forbid")

    offset: int
    limit: int
    returned: int
    total: int
    next_offset: int | None = None


class DailyPageRequest(PageRequest):
    """Request one AESO report date with bounded output pagination."""

    report_date: date


class DateRangePageRequest(PageRequest):
    """Request an inclusive AESO report-date range with bounded output pagination."""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _end_not_before_start(self) -> DateRangePageRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class EnergyMeritOrderBlock(BaseModel):
    """One submitted energy block in a historical merit-order snapshot."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    import_or_export: str | None = None
    asset_id: str | None = None
    block_number: int | None = None
    block_price_cad_per_mwh: float | None = None
    from_mw: float | None = None
    to_mw: float | None = None
    block_size_mw: float | None = None
    available_mw: float | None = None
    dispatched: str | None = None
    dispatched_mw: float | None = None
    flexible: str | None = None
    offer_control: str | None = None


class EnergyMeritOrderResponse(WarningMixin):
    """Paginated historical energy-merit-order blocks."""

    blocks: list[EnergyMeritOrderBlock]
    page: PageInfo
    metadata: DatasetMetadata


class UnitCommitmentDirective(BaseModel):
    """One AESO generating-unit commitment directive."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    issued_at: datetime | None = None
    begins_at: datetime | None = None
    operation_start: datetime | None = None
    operation_end: datetime | None = None


class UnitCommitmentResponse(WarningMixin):
    """Paginated unit-commitment directives."""

    directives: list[UnitCommitmentDirective]
    page: PageInfo
    metadata: DatasetMetadata


class GenerationCapacityInterval(BaseModel):
    """Hourly generation capability and outage grouping for a fuel class."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    fuel_type: str
    sub_fuel_type: str | None = None
    maximum_capability_mw: float | None = None
    mothball_outage_mw: float | None = None
    operating_outage_mw: float | None = None
    available_capability_mw: float | None = None


class GenerationCapacityResponse(WarningMixin):
    """Paginated generation capability and outage observations."""

    intervals: list[GenerationCapacityInterval]
    page: PageInfo
    metadata: DatasetMetadata


class LoadOutageForecastInterval(BaseModel):
    """One hourly AESO load-outage forecast observation."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    load_outage_forecast_mw: float


class LoadOutageForecastResponse(WarningMixin):
    """Paginated load-outage forecast observations."""

    intervals: list[LoadOutageForecastInterval]
    page: PageInfo
    metadata: DatasetMetadata


class IntertieCapabilityRequest(DateRangePageRequest):
    """Request hourly intertie capability, optionally including revision history."""

    start_hour_ending: int = Field(default=1, ge=1, le=24)
    end_hour_ending: int = Field(default=24, ge=1, le=24)
    include_versions: bool = False

    @model_validator(mode="after")
    def _hours_in_order(self) -> IntertieCapabilityRequest:
        if self.start_date == self.end_date and self.end_hour_ending < self.start_hour_ending:
            raise ValueError(
                "end_hour_ending must be on or after start_hour_ending for a single date"
            )
        return self


class IntertieCapabilityInterval(BaseModel):
    """One import or export capability observation for an intertie/flowgate."""

    model_config = ConfigDict(extra="forbid")

    intertie: str
    interval_start: datetime
    hour_ending: int
    direction: Literal["import", "export"]
    flowgate: bool = False
    transfer_type: str | None = None
    reason: str | None = None
    available_transfer_capability_mw: float | None = None
    total_transfer_capability_mw: float | None = None
    transmission_reliability_margin_mw: float | None = None
    system_reliability_margin_mw: float | None = None
    allocation_reliability_margin_mw: float | None = None
    gross_offer_mw: float | None = None
    updated_at: datetime | None = None
    effective_at: datetime | None = None
    is_current: bool = True
    revision_updated_at: datetime | None = None


class IntertieCapabilityResponse(WarningMixin):
    """Paginated intertie capability observations."""

    intervals: list[IntertieCapabilityInterval]
    page: PageInfo
    metadata: DatasetMetadata


class IntertieOutageRecord(BaseModel):
    """One outage affecting an intertie or flowgate."""

    model_config = ConfigDict(extra="forbid")

    element: str
    affected_interties_or_flowgates: list[str] = Field(default_factory=list)
    interval_start: datetime
    interval_end: datetime


class IntertieOutagesResponse(WarningMixin):
    """Paginated outages affecting intertie capability."""

    outages: list[IntertieOutageRecord]
    page: PageInfo
    metadata: DatasetMetadata


class MeteredVolumeRequest(DateRangePageRequest):
    """Request metered volumes, optionally filtered by assets or participants."""

    asset_ids: list[str] = Field(default_factory=list, max_length=20)
    pool_participant_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("asset_ids", "pool_participant_ids")
    @classmethod
    def _individual_identifiers(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item or "," in item:
                raise ValueError("identifiers must be non-empty individual values")
            normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def _only_one_filter_kind(self) -> MeteredVolumeRequest:
        if self.asset_ids and self.pool_participant_ids:
            raise ValueError("Use asset_ids or pool_participant_ids, not both")
        return self


class MeteredVolumeInterval(BaseModel):
    """One hourly metered-volume observation."""

    model_config = ConfigDict(extra="forbid")

    pool_participant_id: str | None = None
    asset_id: str
    asset_class: str | None = None
    interval_start: datetime
    metered_volume_mwh: float


class MeteredVolumeResponse(WarningMixin):
    """Paginated metered-volume observations."""

    intervals: list[MeteredVolumeInterval]
    page: PageInfo
    metadata: DatasetMetadata


class OperatingReserveOfferBlock(BaseModel):
    """One historical operating-reserve offer-control block."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    commodity: str | None = None
    product: str | None = None
    asset_id: str | None = None
    volume_mw: float | None = None
    active_price_cad_per_mwh: float | None = None
    premium_price_cad_per_mwh: float | None = None
    activation_price_cad_per_mwh: float | None = None
    offer_control: str | None = None


class OperatingReserveOfferControlResponse(WarningMixin):
    """Paginated historical operating-reserve offer-control blocks."""

    blocks: list[OperatingReserveOfferBlock]
    page: PageInfo
    metadata: DatasetMetadata


class MarketHistorySummaryRequest(DateRangeRequest):
    """Request compact server-side pool-price and load aggregation."""

    bucket: Literal["hour", "day", "week", "month"] = "day"
    include_load: bool = True


class MarketHistoryBucket(BaseModel):
    """Summary statistics for one market-time bucket."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    price_observations: int
    average_pool_price_cad_per_mwh: float | None = None
    median_pool_price_cad_per_mwh: float | None = None
    minimum_pool_price_cad_per_mwh: float | None = None
    maximum_pool_price_cad_per_mwh: float | None = None
    hours_at_or_above_100_cad_per_mwh: int = 0
    load_observations: int = 0
    average_load_mw: float | None = None
    minimum_load_mw: float | None = None
    maximum_load_mw: float | None = None


class MarketHistorySummaryResponse(WarningMixin):
    """Compact historical market summary suitable for agent context windows."""

    buckets: list[MarketHistoryBucket]
    metadata: DatasetMetadata


class SupplyTightnessResponse(WarningMixin):
    """Current supply-adequacy arithmetic, without a causal market claim."""

    observed_at: datetime
    alberta_internal_load_mw: float | None = None
    available_generation_capability_mw: float | None = None
    maximum_generation_capability_mw: float | None = None
    operating_outage_mw: float | None = None
    mothball_outage_mw: float | None = None
    net_interchange_mw: float | None = None
    contingency_reserve_required_mw: float | None = None
    gross_supply_margin_mw: float | None = None
    reserve_adjusted_margin_mw: float | None = None
    reserve_adjusted_margin_pct_of_load: float | None = None
    tightness_signal: Literal["tight", "watch", "comfortable", "unknown"]
    methodology: str
    metadata: DatasetMetadata
