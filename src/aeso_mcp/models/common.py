# SPDX-License-Identifier: MIT
"""Shared domain models and metadata."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DataStatus(StrEnum):
    """Publication/finality status for returned observations."""

    ACTUAL = "actual"
    FORECAST = "forecast"
    PRELIMINARY = "preliminary"
    FINAL = "final"
    UNKNOWN = "unknown"


class ObservationType(StrEnum):
    """What kind of observation a response contains.

    ``DataStatus`` predates this distinction and remains available for
    compatibility.  ``observation_type`` should be used when a client needs
    to distinguish an actual observation from a forecast or a derived value.
    """

    ACTUAL = "actual"
    FORECAST = "forecast"
    DERIVED = "derived"
    UNKNOWN = "unknown"


class FinalityStatus(StrEnum):
    """Whether published observations are final or still preliminary."""

    FINAL = "final"
    PRELIMINARY = "preliminary"
    UNKNOWN = "unknown"


class DataCompleteness(StrEnum):
    """Completeness of the requested observations or series."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    EMPTY = "empty"
    UNKNOWN = "unknown"


class ProviderName(StrEnum):
    """Which internal adapter produced the data."""

    GRIDSTATUS = "gridstatus"
    AESO_APIM = "aeso_apim"
    AESO_PUBLIC_REPORT = "aeso_public_report"
    DERIVED = "derived"


class DatasetMetadata(BaseModel):
    """Provenance and semantic metadata attached to dataset responses."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["AESO"] = "AESO"
    dataset: str
    source_product: str | None = None
    api_version: str | None = None
    retrieved_at: datetime
    served_at: datetime | None = None
    cache_hit: bool = False
    cache_age: float | None = Field(
        default=None,
        description="Age of the cached provider result in seconds when served.",
    )
    market_timezone: str = "America/Edmonton"
    status: DataStatus = DataStatus.ACTUAL
    observation_type: ObservationType = ObservationType.ACTUAL
    finality: FinalityStatus = FinalityStatus.UNKNOWN
    completeness: DataCompleteness = DataCompleteness.UNKNOWN
    available_series: list[str] = Field(default_factory=list)
    missing_series: list[str] = Field(default_factory=list)
    expected_observations: int | None = None
    missing_observations: int | None = None
    # Count-named aliases keep the relationship with ``observation_count``
    # obvious for clients that prefer explicit field names.
    expected_observation_count: int | None = None
    missing_observation_count: int | None = None
    units: dict[str, str] = Field(default_factory=dict)
    observation_granularity: str | None = None
    request_start: datetime | None = None
    request_end: datetime | None = None
    publication_time: datetime | None = None
    provider: ProviderName = ProviderName.GRIDSTATUS
    observation_count: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class DateRangeRequest(BaseModel):
    """Common bounded date-range request fields."""

    model_config = ConfigDict(extra="forbid")

    start: datetime = Field(
        description=(
            "Inclusive interval start (timezone-aware preferred). "
            "Naive datetimes are interpreted as America/Edmonton market time."
        ),
    )
    end: datetime = Field(
        description=(
            "Exclusive interval end (timezone-aware preferred). "
            "Naive datetimes are interpreted as America/Edmonton market time."
        ),
    )


class WarningMixin(BaseModel):
    """Responses may include non-fatal warnings for clients."""

    warnings: list[str] = Field(default_factory=list)
