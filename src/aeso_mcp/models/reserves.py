# SPDX-License-Identifier: MIT
"""Operating-reserve market contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aeso_mcp.models.common import DatasetMetadata, WarningMixin
from aeso_mcp.models.operations import PageInfo

ReserveType = Literal["regulating", "spinning", "supplemental"]
ReserveProcurement = Literal["active", "standby"]


class OperatingReserveDateRangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    reserve_types: list[ReserveType] = Field(default_factory=list, max_length=3)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)

    @model_validator(mode="after")
    def _ordered(self) -> OperatingReserveDateRangeRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date.")
        if len(self.reserve_types) != len(set(self.reserve_types)):
            raise ValueError("reserve_types must be unique.")
        return self


class OperatingReservePriceInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market_date: date
    procurement: ReserveProcurement
    reserve_type: ReserveType
    time_block: str
    active_price_cad_per_mw: float | None = None
    premium_price_cad_per_mw: float | None = None
    activation_price_cad_per_mwh: float | None = None
    clearing_blended_price_cad_per_mw: float | None = None
    volume_mw: float | None = None


class OperatingReservePricesResponse(WarningMixin):
    intervals: list[OperatingReservePriceInterval]
    page: PageInfo
    metadata: DatasetMetadata


class OperatingReserveForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reserve_types: list[ReserveType] = Field(default_factory=list, max_length=3)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)


class OperatingReserveForecastInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_start: datetime
    interval_end: datetime
    active_regulating_mw: float | None = None
    active_spinning_mw: float | None = None
    active_supplemental_mw: float | None = None
    standby_regulating_mw: float | None = None
    standby_spinning_mw: float | None = None
    standby_supplemental_mw: float | None = None


class OperatingReserveForecastResponse(WarningMixin):
    intervals: list[OperatingReserveForecastInterval]
    page: PageInfo
    metadata: DatasetMetadata


class OperatingReserveActivationInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_start: datetime
    interval_end: datetime
    service_level: str
    reserve_type: ReserveType
    activated_volume_mw: float
    weighted_average_activation_price_cad_per_mwh: float


class OperatingReserveActivationsResponse(WarningMixin):
    intervals: list[OperatingReserveActivationInterval]
    page: PageInfo
    metadata: DatasetMetadata


class OperatingReserveSummaryRequest(OperatingReserveDateRangeRequest):
    include_activations: bool = True


class OperatingReserveSummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    procurement: ReserveProcurement
    reserve_type: ReserveType
    observation_count: int
    average_price_cad_per_mw: float | None = None
    minimum_price_cad_per_mw: float | None = None
    maximum_price_cad_per_mw: float | None = None
    average_volume_mw: float | None = None
    activated_volume_mw: float = 0.0
    average_activation_price_cad_per_mwh: float | None = None


class OperatingReserveSummaryResponse(WarningMixin):
    results: list[OperatingReserveSummaryResult]
    methodology: str
    metadata: DatasetMetadata
