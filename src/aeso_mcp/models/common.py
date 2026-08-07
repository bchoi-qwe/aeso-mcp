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


class ProviderName(StrEnum):
    """Which internal adapter produced the data."""

    GRIDSTATUS = "gridstatus"
    AESO_APIM = "aeso_apim"
    DERIVED = "derived"


class DatasetMetadata(BaseModel):
    """Provenance and semantic metadata attached to dataset responses."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["AESO"] = "AESO"
    dataset: str
    source_product: str | None = None
    api_version: str | None = None
    retrieved_at: datetime
    market_timezone: str = "America/Edmonton"
    status: DataStatus = DataStatus.ACTUAL
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
