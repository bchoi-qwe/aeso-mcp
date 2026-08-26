# SPDX-License-Identifier: MIT
"""Historical-research data contracts."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DateRangeRequest,
    FinalityStatus,
    ObservationType,
    WarningMixin,
)
from aeso_mcp.models.operations import PageInfo


class HistoricalDataset(StrEnum):
    """Datasets supported by the internal historical store."""

    HISTORICAL_GENERATION = "historical_generation"
    POOL_PRICE = "pool_price"
    LOAD = "load"


class HistoricalGenerationRequest(DateRangeRequest):
    """Retrieve individual-asset CSD generation from the AESO archive."""

    interval: Literal["hourly", "5-minute"] = "hourly"
    asset_ids: list[str] = Field(default_factory=list, max_length=50)
    fuel_types: list[str] = Field(default_factory=list, max_length=25)
    refresh: bool = Field(
        default=False,
        description="Recheck and replace intersecting upstream archive files before querying.",
    )
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)

    @field_validator("asset_ids", "fuel_types")
    @classmethod
    def _normalize_filters(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Filter values must be unique.")
        return normalized


class HistoricalGenerationInterval(BaseModel):
    """One archived individual-asset CSD generation observation."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    interval_start_utc: datetime
    interval_end_utc: datetime
    resolution: Literal["hourly", "5-minute"]
    asset_id: str
    asset_name: str | None = None
    asset_grouping: str | None = None
    fuel_type: str
    sub_fuel_type: str | None = None
    generation_mw: float
    maximum_capability_mw: float | None = None
    system_capability_mw: float | None = None
    planning_area: str | None = None
    region: str | None = None
    source_file_id: str
    source_file_name: str
    source_updated_at: datetime | None = None
    source_retrieved_at: datetime
    observation_type: ObservationType = ObservationType.ACTUAL
    finality: FinalityStatus = FinalityStatus.UNKNOWN
    completeness: DataCompleteness = DataCompleteness.COMPLETE
    schema_version: int = 1


class HistoricalGenerationResponse(WarningMixin):
    """Bounded individual-asset historical generation results."""

    intervals: list[HistoricalGenerationInterval]
    page: PageInfo
    metadata: DatasetMetadata


class HistoricalStoreSyncRequest(DateRangeRequest):
    """Incrementally ingest supported datasets into DuckDB/Parquet."""

    datasets: list[HistoricalDataset] = Field(
        default_factory=lambda: [
            HistoricalDataset.HISTORICAL_GENERATION,
            HistoricalDataset.POOL_PRICE,
            HistoricalDataset.LOAD,
        ],
        min_length=1,
        max_length=3,
    )
    generation_interval: Literal["hourly", "5-minute"] = "hourly"
    refresh: bool = False

    @field_validator("datasets")
    @classmethod
    def _unique_datasets(cls, values: list[HistoricalDataset]) -> list[HistoricalDataset]:
        if len(values) != len(set(values)):
            raise ValueError("datasets must not contain duplicates.")
        return values


class HistoricalSyncDatasetResult(BaseModel):
    """Observable outcome for one dataset in an incremental sync."""

    model_config = ConfigDict(extra="forbid")

    dataset: HistoricalDataset
    records_received: int
    records_written: int
    source_files_checked: int = 0
    source_files_downloaded: int = 0
    partitions_rebuilt: int = 0
    missing_interval_count: int = 0
    duplicate_records_detected: int = 0


class HistoricalStoreSyncResponse(WarningMixin):
    """Incremental ingestion outcome and provenance."""

    results: list[HistoricalSyncDatasetResult]
    metadata: DatasetMetadata


class HistoricalDatasetStatus(BaseModel):
    """Coverage status for one stored dataset."""

    model_config = ConfigDict(extra="forbid")

    dataset: HistoricalDataset
    observation_count: int
    earliest_interval: datetime | None = None
    latest_interval: datetime | None = None
    source_file_count: int = 0
    parquet_partition_count: int = 0
    detected_gap_count: int = 0
    schema_version: int = 1


class HistoricalStoreStatusResponse(WarningMixin):
    """Read-only status for the configured historical store."""

    store_path: str
    enabled: bool
    datasets: list[HistoricalDatasetStatus]
    metadata: DatasetMetadata


ForecastSeries = Literal["ail"]


class ForecastRequest(DateRangeRequest):
    """Retrieve paired actual/forecast observations for a supported series."""

    series: ForecastSeries = "ail"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)


class ForecastInterval(BaseModel):
    """One actual/forecast pair with explicit units and source semantics."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    series: ForecastSeries
    actual_value: float
    forecast_value: float | None = None
    unit: str


class ForecastResponse(WarningMixin):
    """General forecast observations; AIL is the current supported series."""

    intervals: list[ForecastInterval]
    page: PageInfo
    metadata: DatasetMetadata


class UnitCommitmentSettlementRequest(BaseModel):
    """Retrieve public UC settlement summary observations for a date range."""

    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)

    @model_validator(mode="after")
    def _valid_range(self) -> UnitCommitmentSettlementRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date.")
        return self


class UnitCommitmentSettlementInterval(BaseModel):
    """One hourly UC settlement payment/charged-volume observation."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    hour_ending_label: str
    total_uc_amount_cad: float
    total_charged_volume_mw: float


class UnitCommitmentSettlementResponse(WarningMixin):
    """Public UC settlement summary results."""

    intervals: list[UnitCommitmentSettlementInterval]
    page: PageInfo
    metadata: DatasetMetadata
