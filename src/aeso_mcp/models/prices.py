# SPDX-License-Identifier: MIT
"""Price-related domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aeso_mcp.models.common import DatasetMetadata, DateRangeRequest, WarningMixin
from aeso_mcp.models.operations import PageInfo


class PoolPriceRequest(DateRangeRequest):
    """Request hourly Alberta Pool Price observations."""

    include_forecast: bool = Field(
        default=False,
        description="When true, also include AESO forecast pool price when present.",
    )
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)


class PoolPriceInterval(BaseModel):
    """One hourly pool-price settlement interval."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    pool_price_cad_per_mwh: float
    forecast_pool_price_cad_per_mwh: float | None = None
    rolling_30day_avg_cad_per_mwh: float | None = None


class PoolPriceResponse(WarningMixin):
    """Hourly Pool Price observations in CAD/MWh."""

    intervals: list[PoolPriceInterval]
    page: PageInfo
    metadata: DatasetMetadata


class SystemMarginalPriceRequest(DateRangeRequest):
    """Request minute-level System Marginal Price (SMP) observations."""

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)


class SystemMarginalPriceInterval(BaseModel):
    """One SMP observation interval."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    system_marginal_price_cad_per_mwh: float


class SystemMarginalPriceResponse(WarningMixin):
    """Minute-level System Marginal Price observations in CAD/MWh."""

    intervals: list[SystemMarginalPriceInterval]
    page: PageInfo
    metadata: DatasetMetadata
