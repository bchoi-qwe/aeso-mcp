# SPDX-License-Identifier: MIT
"""Grid operations domain models (interchange, reserves, snapshot, outages)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aeso_mcp.models.common import DatasetMetadata, DateRangeRequest, WarningMixin
from aeso_mcp.models.generation import FuelMixComponent


class InterchangePathFlow(BaseModel):
    """Flow on a single intertie path (MW). Positive typically indicates import."""

    model_config = ConfigDict(extra="forbid")

    path: str
    flow_mw: float


class InterchangeResponse(WarningMixin):
    """Current interchange flows."""

    observed_at: datetime
    paths: list[InterchangePathFlow]
    net_interchange_mw: float
    metadata: DatasetMetadata


class ReservesResponse(WarningMixin):
    """Current operating reserve conditions."""

    observed_at: datetime
    contingency_reserve_required_mw: float | None = None
    dispatched_contingency_reserve_total_mw: float | None = None
    dispatched_contingency_reserve_gen_mw: float | None = None
    dispatched_contingency_reserve_other_mw: float | None = None
    fast_frequency_response_dispatched_mw: float | None = None
    fast_frequency_response_offered_mw: float | None = None
    long_lead_time_volume_mw: float | None = None
    metadata: DatasetMetadata


class MarketSnapshotResponse(WarningMixin):
    """Cohesive current-state view of the Alberta electricity market."""

    observed_at: datetime
    pool_price_cad_per_mwh: float | None = None
    system_marginal_price_cad_per_mwh: float | None = None
    alberta_internal_load_mw: float | None = None
    total_generation_mw: float | None = None
    generation_by_fuel: list[FuelMixComponent] = Field(default_factory=list)
    wind_generation_mw: float | None = None
    solar_generation_mw: float | None = None
    renewable_share: float | None = None
    net_interchange_mw: float | None = None
    interchange_paths: list[InterchangePathFlow] = Field(default_factory=list)
    contingency_reserve_required_mw: float | None = None
    dispatched_contingency_reserve_total_mw: float | None = None
    metadata: DatasetMetadata


class OutagesRequest(DateRangeRequest):
    """Request generator outage reports."""


class OutageRecord(BaseModel):
    """One generator outage observation."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime | None = None
    asset_id: str | None = None
    asset_name: str | None = None
    fuel_type: str | None = None
    outage_mw: float | None = None
    maximum_capability_mw: float | None = None
    raw: dict[str, str | float | int | None] = Field(default_factory=dict)


class OutagesResponse(WarningMixin):
    """Generator outage observations."""

    outages: list[OutageRecord]
    metadata: DatasetMetadata
