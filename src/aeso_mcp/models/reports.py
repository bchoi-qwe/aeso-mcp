# SPDX-License-Identifier: MIT
"""Typed contracts for official AESO public operational reports."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DateRangeRequest,
    FinalityStatus,
    ObservationType,
    WarningMixin,
)
from aeso_mcp.models.operations import PageInfo


class PublicReportDateRangeRequest(DateRangeRequest):
    """Bounded market-time range shared by historical public reports."""

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)


class SupplyAdequacyRequest(BaseModel):
    """Optional filter for the current seven-day adequacy publication."""

    model_config = ConfigDict(extra="forbid")

    start: datetime | None = None
    end: datetime | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)

    @model_validator(mode="after")
    def _paired_range(self) -> SupplyAdequacyRequest:
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end must be provided together.")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("end must be after start.")
        return self


class SupplyAdequacyInterval(BaseModel):
    """One hourly categorical adequacy/cushion observation.

    The current AESO report publishes status bands rather than a numeric
    demand/supply pair.  Numeric fields remain optional so a future named
    report can be added without changing the outer contract; this parser does
    not infer them from the bands.
    """

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    adequacy_status_code: int | None = None
    adequacy_status: str | None = None
    supply_cushion_code: int | None = None
    supply_cushion_status: str | None = None
    forecast_demand_mw: float | None = None
    available_supply_mw: float | None = None
    supply_cushion_mw: float | None = None
    publication_time: datetime | None = None
    observation_type: ObservationType = ObservationType.FORECAST
    finality: FinalityStatus = FinalityStatus.PRELIMINARY
    completeness: DataCompleteness = DataCompleteness.COMPLETE


class SupplyAdequacyResponse(WarningMixin):
    """Paginated AESO supply-adequacy and market-supply-cushion rows."""

    intervals: list[SupplyAdequacyInterval]
    page: PageInfo
    metadata: DatasetMetadata


SupplySurplusStatus = Literal[
    "all_forecast_prices_positive",
    "some_zero_forecast_prices",
    "all_zero_forecast_prices",
]


class SupplySurplusRequest(SupplyAdequacyRequest):
    """Optional filter for the current supply-surplus status publication."""


class SupplySurplusInterval(BaseModel):
    """One hourly supply-surplus forecast/status row."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    status_code: int
    status: SupplySurplusStatus
    publication_time: datetime | None = None
    observation_type: ObservationType = ObservationType.FORECAST
    finality: FinalityStatus = FinalityStatus.PRELIMINARY
    completeness: DataCompleteness = DataCompleteness.COMPLETE


class SupplySurplusResponse(WarningMixin):
    """Paginated supply-surplus status rows from the AESO named report."""

    intervals: list[SupplySurplusInterval]
    page: PageInfo
    metadata: DatasetMetadata


class SupplySurplusEventsRequest(SupplySurplusRequest):
    """Request contiguous supply-surplus states derived from published rows."""


class SupplySurplusEvent(BaseModel):
    """An explicitly bounded supply-surplus event derived from status rows.

    ``end`` is present only when contiguous report intervals provide an
    explicit next boundary.  A report's current status alone never creates an
    assumed event end.
    """

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime | None = None
    status: SupplySurplusStatus
    status_code: int
    source_confidence: Literal["published_status", "published_event_log"]
    comments: str | None = None


class SupplySurplusEventsResponse(WarningMixin):
    """Published-status supply-surplus events with only explicit end bounds."""

    events: list[SupplySurplusEvent]
    page: PageInfo
    metadata: DatasetMetadata


class FfrNetScheduleRequest(PublicReportDateRangeRequest):
    """Request the bounded official FFR Net Schedule archive."""


class FfrNetScheduleInterval(BaseModel):
    """One scheduled intertie-transfer row used in the FFR Net Schedule.

    AESO defines imports as negative and exports as positive.  Component
    interties are retained separately from the net schedule.
    """

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    net_schedule_mw: float
    bc_intertie_mw: float | None = None
    matl_intertie_mw: float | None = None
    sask_intertie_mw: float | None = None
    observation_type: ObservationType = ObservationType.ACTUAL
    finality: FinalityStatus = FinalityStatus.FINAL
    completeness: DataCompleteness = DataCompleteness.COMPLETE


class FfrNetScheduleResponse(WarningMixin):
    intervals: list[FfrNetScheduleInterval]
    page: PageInfo
    metadata: DatasetMetadata


class DdsMarketReportRequest(PublicReportDateRangeRequest):
    """Request point-in-time Dispatch Down Service availability records."""


class DdsAvailabilityRecord(BaseModel):
    """One AESO DDS available-volume publication."""

    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    available_dds_mw: float
    observation_type: ObservationType = ObservationType.ACTUAL
    finality: FinalityStatus = FinalityStatus.PRELIMINARY
    completeness: DataCompleteness = DataCompleteness.COMPLETE


class DdsMarketReportResponse(WarningMixin):
    records: list[DdsAvailabilityRecord]
    page: PageInfo
    metadata: DatasetMetadata


class TmrReferencePriceRequest(BaseModel):
    """Optional date filter for the monthly TMR reference-price report."""

    model_config = ConfigDict(extra="forbid")

    start_date: date | None = None
    end_date: date | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)

    @model_validator(mode="after")
    def _ordered(self) -> TmrReferencePriceRequest:
        if self.start_date is not None and self.end_date is not None:
            if self.end_date < self.start_date:
                raise ValueError("end_date must be on or after start_date.")
        elif self.start_date is not None or self.end_date is not None:
            raise ValueError("start_date and end_date must be provided together.")
        return self


class TmrReferencePrice(BaseModel):
    """One published monthly Transmission Must-Run reference price."""

    model_config = ConfigDict(extra="forbid")

    effective_date: date
    reference_price_cad_per_mwh: float
    observation_type: ObservationType = ObservationType.ACTUAL
    finality: FinalityStatus = FinalityStatus.FINAL
    completeness: DataCompleteness = DataCompleteness.COMPLETE


class TmrReferencePriceResponse(WarningMixin):
    records: list[TmrReferencePrice]
    page: PageInfo
    metadata: DatasetMetadata


SystemEventType = Literal[
    "supply_surplus",
    "energy_emergency",
    "outage",
    "maintenance",
    "market_operation",
    "other",
]


class SystemEventsRequest(PublicReportDateRangeRequest):
    """Request bounded AIES Event Log entries."""


class SystemEvent(BaseModel):
    """One published AIES Event Log message.

    Event classification is keyword-based labeling of the published comment;
    the raw comment remains authoritative and no event end is inferred.
    """

    model_config = ConfigDict(extra="forbid")

    event_time: datetime
    comments: str
    event_type: SystemEventType = "other"
    observation_type: ObservationType = ObservationType.ACTUAL
    finality: FinalityStatus = FinalityStatus.UNKNOWN
    completeness: DataCompleteness = DataCompleteness.COMPLETE


class SystemEventsResponse(WarningMixin):
    records: list[SystemEvent]
    page: PageInfo
    metadata: DatasetMetadata
