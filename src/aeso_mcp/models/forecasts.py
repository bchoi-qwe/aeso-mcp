# SPDX-License-Identifier: MIT
"""Typed contracts for official AESO forecast products.

The existing historical service exposes an AIL-compatible forecast contract.
This module is the source-oriented contract for AESO's renewable and
pool-price forecast reports.  A row may contain both the published forecast
and the later actual value; the two values are never substituted for one
another.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
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

ForecastSeries = Literal["ail", "pool_price", "wind", "solar", "wind_solar"]
ForecastHorizon = Literal["current_12_hour", "current_7_day", "historical"]


class OfficialForecastRequest(DateRangeRequest):
    """Bounded request for an official AESO forecast series.

    ``horizon`` is required by current renewable files (the 12-hour and
    seven-day reports have different cadence and revision behaviour), while
    historical actual-vs-forecast files use ``historical``.  The field is
    optional to keep callers able to request the shared AIL/pool-price series;
    providers choose the only valid source for those products.
    """

    model_config = ConfigDict(extra="forbid")

    series: ForecastSeries = "ail"
    horizon: ForecastHorizon | None = None
    include_actual: bool = True
    as_of: datetime | None = Field(
        default=None,
        description=(
            "Return only forecast information published or issued no later than this "
            "timezone-aware instant. When omitted, the latest available vintage is returned."
        ),
    )
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)

    @model_validator(mode="after")
    def _as_of_is_aware(self) -> OfficialForecastRequest:
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware.")
        return self


class ForecastInterval(BaseModel):
    """One target interval from an AESO forecast publication.

    ``forecast_value`` is the source's forecast (for wind and solar this is
    the ``Most Likely`` value); ``minimum_value`` and ``maximum_value`` retain
    the published forecast band when that product provides one.  ``actual``
    is populated only when the source explicitly publishes it.
    """

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    series: ForecastSeries
    horizon: ForecastHorizon | None = None
    forecast_value: float | None = None
    actual_value: float | None = None
    minimum_value: float | None = None
    maximum_value: float | None = None
    capacity_mw: float | None = None
    forecast_issue_time: datetime | None = None
    publication_time: datetime | None = None
    retrieved_at: datetime | None = None
    source_version: str | None = None
    source_hash: str | None = None
    source_file_id: str | None = None
    source_file_name: str | None = None
    vintage_id: str | None = None
    lead_time_minutes: int | None = Field(default=None, ge=0)
    unit: str
    source_product: str
    observation_type: ObservationType = ObservationType.FORECAST
    finality: FinalityStatus = FinalityStatus.UNKNOWN
    completeness: DataCompleteness = DataCompleteness.COMPLETE

    @model_validator(mode="before")
    @classmethod
    def _accept_target_aliases(cls, values: object) -> object:
        if not isinstance(values, Mapping):
            return values
        normalized = dict(values)
        aliases = {
            "target_interval_start": "interval_start",
            "target_interval_end": "interval_end",
            "issue_time": "forecast_issue_time",
        }
        for alias, field in aliases.items():
            if field not in normalized and alias in normalized:
                normalized[field] = normalized[alias]
            normalized.pop(alias, None)
        return normalized

    @model_validator(mode="after")
    def _has_value(self) -> ForecastInterval:
        if self.forecast_value is None and self.actual_value is None:
            raise ValueError("A forecast interval must contain a forecast or actual value.")
        return self


class ForecastVintage(BaseModel):
    """One immutable forecast publication for one target interval.

    A forecast vintage is identified by ``vintage_id``.  The ID is derived from
    the series, target interval, issue/publication timestamps, source version,
    and canonical forecast-only payload by the historical store.  It is
    deliberately independent of retrieval time, raw content hash, later actual
    enrichment, finality, and completeness so repeated ingestion deduplicates
    the same publication.
    ``ForecastInterval`` remains the response-facing type so existing callers
    continue to receive the same semantic abstraction.
    """

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    series: ForecastSeries
    horizon: ForecastHorizon | None = None
    forecast_value: float | None = None
    actual_value: float | None = None
    minimum_value: float | None = None
    maximum_value: float | None = None
    capacity_mw: float | None = None
    forecast_issue_time: datetime | None = None
    publication_time: datetime | None = None
    retrieved_at: datetime | None = None
    source_version: str | None = None
    source_hash: str | None = None
    source_file_id: str | None = None
    source_file_name: str | None = None
    vintage_id: str | None = None
    schema_version: int = Field(default=3, ge=1)
    lead_time_minutes: int | None = Field(default=None, ge=0)
    unit: str
    source_product: str
    observation_type: ObservationType = ObservationType.FORECAST
    finality: FinalityStatus = FinalityStatus.UNKNOWN
    completeness: DataCompleteness = DataCompleteness.COMPLETE

    @model_validator(mode="before")
    @classmethod
    def _accept_storage_aliases(cls, values: object) -> object:
        if not isinstance(values, Mapping):
            return values
        normalized = dict(values)
        aliases = {
            "target_interval_start": "interval_start",
            "target_interval_end": "interval_end",
            "issue_time": "forecast_issue_time",
            "source_retrieved_at": "retrieved_at",
        }
        for alias, field in aliases.items():
            if field not in normalized and alias in normalized:
                normalized[field] = normalized[alias]
            normalized.pop(alias, None)
        return normalized

    @model_validator(mode="after")
    def _has_value(self) -> ForecastVintage:
        if self.forecast_value is None and self.actual_value is None:
            raise ValueError("A forecast vintage must contain a forecast or actual value.")
        return self

    @property
    def target_interval_start(self) -> datetime:
        """Alias used by storage/research code for the forecast target."""

        return self.interval_start

    @property
    def target_interval_end(self) -> datetime:
        """Alias used by storage/research code for the forecast target."""

        return self.interval_end

    @property
    def issue_time(self) -> datetime | None:
        """Short alias for the source forecast issue timestamp."""

        return self.forecast_issue_time


class ForecastResponse(WarningMixin):
    """Paginated official forecast observations with source metadata."""

    intervals: list[ForecastInterval]
    page: PageInfo
    metadata: DatasetMetadata


class ForecastErrorSummary(BaseModel):
    """Deterministic paired forecast-error statistics."""

    model_config = ConfigDict(extra="forbid")

    series: ForecastSeries
    observation_count: int
    excluded_without_forecast: int = 0
    excluded_without_actual: int = 0
    mean_error: float | None = None
    mean_absolute_error: float | None = None
    rmse: float | None = None
    mape: float | None = None
    p50_absolute_error: float | None = None
    p90_absolute_error: float | None = None
    p95_absolute_error: float | None = None
    unit: str
    error_definition: str = "forecast minus actual"


class ForecastErrorResponse(WarningMixin):
    """Forecast-error summary and provenance."""

    summary: ForecastErrorSummary
    metadata: DatasetMetadata
