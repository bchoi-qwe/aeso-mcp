# SPDX-License-Identifier: MIT
"""Typed contracts for the official AESO research-data archive.

The data-request files are deliberately represented as observations rather
than as a collection of source-specific MCP tools.  ``dataset`` is the
discriminator and the fields on each row retain the source's units and
semantics.  In particular, no ownership, causality, or numeric supply cushion
is inferred from a categorical web code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aeso_mcp.models.common import DatasetMetadata, DateRangeRequest, WarningMixin
from aeso_mcp.models.operations import PageInfo

ResearchDataset = Literal[
    "supply_adequacy",
    "supply_cushion",
    "transmission_outages",
    "planning_area",
    "constrained_volume",
    "eea_events",
    "or_directives",
    "system_frequency",
    "pool_participants",
]


class SupplyAdequacyHistoryInterval(BaseModel):
    """One historical Supply Adequacy web-code observation.

    AESO publishes the last forecast web code for each historical hour.  The
    web code is not a numeric MW cushion and is therefore kept as a code.
    """

    model_config = ConfigDict(extra="forbid")

    dataset: Literal["supply_adequacy"] = "supply_adequacy"
    interval_start: datetime
    interval_end: datetime
    version_start: datetime
    web_code: int = Field(ge=0)


class SupplyCushionHistoryInterval(BaseModel):
    """One historical Market Supply Cushion web-code observation."""

    model_config = ConfigDict(extra="forbid")

    dataset: Literal["supply_cushion"] = "supply_cushion"
    interval_start: datetime
    interval_end: datetime
    version_start: datetime
    web_code: int = Field(ge=0)


class TransmissionOutageHistoryRecord(BaseModel):
    """One historical AESO transmission-outage record.

    ``interval_end`` is the source's planned end.  It is not an observed
    restoration time; the original descriptive columns remain available.
    """

    model_config = ConfigDict(extra="forbid")

    dataset: Literal["transmission_outages"] = "transmission_outages"
    interval_start: datetime
    interval_end: datetime
    owner: str
    name: str
    tie_line: str | None = None
    significant_outage: str | None = None
    activity: str | None = None
    equipment_type: str | None = None
    outage_event: str | None = None
    outage_reason: str | None = None
    outage_condition: str | None = None


class PlanningAreaLoadGenerationInterval(BaseModel):
    """One hourly planning-area load/generation observation.

    AESO defines ``Load`` and ``SysGen`` as metered system load/generation;
    ``CSDGen`` is generation at assets at least 5 MW, ``BTFGen`` is
    ``CSDGen - SysGen``, and ``ActLoad`` is ``BTFGen + Load``.  The source
    names are retained in the field descriptions while values are exposed in
    MW for callers.
    """

    model_config = ConfigDict(extra="forbid")

    dataset: Literal["planning_area"] = "planning_area"
    interval_start: datetime
    interval_end: datetime
    region: str
    planning_area: str
    load_mw: float | None = None
    system_generation_mw: float | None = None
    csd_generation_mw: float | None = None
    behind_the_fence_generation_mw: float | None = None
    actual_load_mw: float | None = None


class ConstrainedVolumeInterval(BaseModel):
    """One hourly constrained-volume row from AESO's fixed CSV archive."""

    model_config = ConfigDict(extra="forbid")

    dataset: Literal["constrained_volume"] = "constrained_volume"
    interval_start: datetime
    interval_end: datetime
    fuel_type: str
    planning_area: str
    constrained_volume_mwh: float
    constrained_minutes: int = Field(ge=0, le=60)


class EeaEventRecord(BaseModel):
    """One historical Energy Emergency Alert or Grid Alert row.

    AESO states that post-2022 Grid Alerts are represented as EEA3 in this
    archive; that source convention is preserved in ``eea_level``.
    """

    model_config = ConfigDict(extra="forbid")

    dataset: Literal["eea_events"] = "eea_events"
    interval_start: datetime
    interval_end: datetime
    eea_level: str
    duration_hours: float = Field(ge=0)
    duration_minutes: float = Field(ge=0)
    comments: str | None = None


class OrDirectiveRecord(BaseModel):
    """One aggregated operating-reserve directive/service row.

    The March-August 2023 publication labels its capacity column ``Capacity
    (MW)`` while the other published periods label it ``Time-Weighted Average
    Capacity (MW)``. Keep those source semantics distinct instead of silently
    presenting the former as a time-weighted value.
    """

    model_config = ConfigDict(extra="forbid")

    dataset: Literal["or_directives"] = "or_directives"
    interval_start: datetime
    interval_end: datetime
    event_number: int = Field(ge=1)
    service: str
    time_weighted_average_capacity_mw: float | None = None
    capacity_mw: float | None = None
    time_weighted_average_directive_mw: float
    maximum_directive_mw: float
    event_duration_seconds: int = Field(ge=0)
    energy_mwh: float
    max_duration_seconds: float | None = Field(default=None, ge=0)
    min_duration_seconds: float | None = Field(default=None, ge=0)


class SystemFrequencyInterval(BaseModel):
    """One 10-second system-frequency observation."""

    model_config = ConfigDict(extra="forbid")

    dataset: Literal["system_frequency"] = "system_frequency"
    interval_start: datetime
    interval_end: datetime
    average_frequency_hz: float
    maximum_frequency_hz: float
    minimum_frequency_hz: float


class PoolParticipantAgent(BaseModel):
    """An agent listed by AESO for a pool participant."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    agent_name: str | None = None


class PoolParticipantRecord(BaseModel):
    """One current AESO Pool Participant API record.

    This is a current registry, not a historical ownership record.  The
    ``agent_list`` is kept separate because an agent is not necessarily the
    participant's owner or corporate parent.
    """

    model_config = ConfigDict(extra="forbid")

    dataset: Literal["pool_participants"] = "pool_participants"
    pool_participant_id: str
    pool_participant_name: str
    corporate_contact: str | None = None
    agents: list[PoolParticipantAgent] = Field(default_factory=list)


type ResearchRecord = Annotated[
    SupplyAdequacyHistoryInterval
    | SupplyCushionHistoryInterval
    | TransmissionOutageHistoryRecord
    | PlanningAreaLoadGenerationInterval
    | ConstrainedVolumeInterval
    | EeaEventRecord
    | OrDirectiveRecord
    | SystemFrequencyInterval
    | PoolParticipantRecord,
    Field(discriminator="dataset"),
]


class ResearchDataRequest(BaseModel):
    """Bounded request for one member of the official archive abstraction."""

    model_config = ConfigDict(extra="forbid")

    dataset: ResearchDataset
    start: datetime | None = None
    end: datetime | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2_000)
    planning_area: str | None = None
    region: str | None = None
    fuel_type: str | None = None
    pool_participant_ids: list[str] = Field(default_factory=list, max_length=20)
    pool_participant_name: str | None = None
    frequency_resolution: Literal["10s"] = "10s"

    @field_validator("pool_participant_ids")
    @classmethod
    def _normalize_participant_ids(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values]

    @model_validator(mode="after")
    def _validate_range_and_filters(self) -> ResearchDataRequest:
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end must be provided together.")
        if self.dataset == "pool_participants":
            if self.start is not None or self.end is not None:
                raise ValueError("pool_participants is a current registry and takes no date range.")
        elif self.start is None or self.end is None:
            raise ValueError(f"start and end are required for {self.dataset}.")
        if self.pool_participant_ids and self.dataset != "pool_participants":
            raise ValueError("pool_participant_ids are only valid for pool_participants.")
        if self.pool_participant_name is not None and self.dataset != "pool_participants":
            raise ValueError("pool_participant_name is only valid for pool_participants.")
        if self.planning_area is not None and self.dataset not in {
            "planning_area",
            "constrained_volume",
        }:
            raise ValueError("planning_area is only valid for planning_area or constrained_volume.")
        if self.region is not None and self.dataset != "planning_area":
            raise ValueError("region is only valid for planning_area.")
        if self.fuel_type is not None and self.dataset != "constrained_volume":
            raise ValueError("fuel_type is only valid for constrained_volume.")
        for value in self.pool_participant_ids:
            if not value.strip() or "," in value:
                raise ValueError("pool_participant_ids must contain individual non-empty values.")
        if self.pool_participant_name is not None and not self.pool_participant_name.strip():
            raise ValueError("pool_participant_name must not be blank.")
        return self


class ResearchDataResponse(WarningMixin):
    """Paginated records from one official AESO research-data publication."""

    records: list[ResearchRecord]
    page: PageInfo
    metadata: DatasetMetadata


class ParticipantConcentrationRequest(DateRangeRequest):
    """Request participant concentration over historical merit-order blocks."""

    top_n: int = Field(default=5, ge=1, le=20)


class ParticipantConcentrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool_participant_id: str
    pool_participant_name: str | None = None
    offered_volume_mw: float
    offered_volume_share: float | None = None
    asset_count: int = Field(ge=0)
    block_count: int = Field(ge=0)


class ParticipantConcentrationResponse(WarningMixin):
    results: list[ParticipantConcentrationResult]
    total_offered_volume_mw: float
    hhi: float | None = None
    top_three_share: float | None = None
    top_five_share: float | None = None
    matched_block_count: int
    unmapped_block_count: int
    methodology: str
    metadata: DatasetMetadata


class RegionalAnalysisRequest(DateRangeRequest):
    """Request compact planning-area or regional load/generation aggregates."""

    planning_area: str | None = None
    region: str | None = None
    limit: int = Field(default=200, ge=1, le=2_000)


class RegionalAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    planning_area: str
    observation_count: int = Field(ge=0)
    average_load_mw: float | None = None
    average_system_generation_mw: float | None = None
    average_csd_generation_mw: float | None = None
    average_behind_the_fence_generation_mw: float | None = None
    average_actual_load_mw: float | None = None
    peak_actual_load_mw: float | None = None
    total_actual_load_mwh: float | None = None


class RegionalAnalysisResponse(WarningMixin):
    results: list[RegionalAnalysisResult]
    methodology: str
    metadata: DatasetMetadata


class CongestionAnalysisRequest(DateRangeRequest):
    """Request descriptive constrained-volume summaries."""

    planning_area: str | None = None
    fuel_type: str | None = None
    include_pool_price: bool = True
    limit: int = Field(default=200, ge=1, le=2_000)


class CongestionAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planning_area: str
    fuel_type: str
    constrained_observations: int = Field(ge=0)
    constrained_volume_mwh: float
    constrained_minutes: int = Field(ge=0)
    average_pool_price_cad_per_mwh: float | None = None
    matched_pool_price_observations: int = Field(ge=0)


class CongestionAnalysisResponse(WarningMixin):
    results: list[CongestionAnalysisResult]
    methodology: str
    metadata: DatasetMetadata


class ScarcityAnalysisRequest(DateRangeRequest):
    """Request categorical adequacy/cushion and EEA context."""

    high_price_threshold_cad_per_mwh: float = Field(default=100.0, ge=0)


class ScarcityAnalysisResponse(WarningMixin):
    observation_count: int
    adequacy_code_counts: dict[int, int]
    cushion_code_counts: dict[int, int]
    eea_event_count: int
    eea_level_counts: dict[str, int]
    high_price_observation_count: int
    average_pool_price_cad_per_mwh: float | None = None
    methodology: str
    metadata: DatasetMetadata


class FrequencyAnalysisRequest(DateRangeRequest):
    """Bounded request for compact 10-second frequency statistics."""

    lower_threshold_hz: float = Field(default=59.95, ge=0)
    upper_threshold_hz: float = Field(default=60.05, ge=0)

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> FrequencyAnalysisRequest:
        if self.upper_threshold_hz <= self.lower_threshold_hz:
            raise ValueError("upper_threshold_hz must be greater than lower_threshold_hz.")
        return self


class FrequencyAnalysisResponse(WarningMixin):
    observation_count: int
    average_frequency_hz: float | None = None
    minimum_frequency_hz: float | None = None
    maximum_frequency_hz: float | None = None
    p05_frequency_hz: float | None = None
    p95_frequency_hz: float | None = None
    flagged_interval_seconds_below_lower_threshold: float = Field(
        default=0.0,
        description=(
            "10-second interval exposure proxy/upper bound: each interval whose source minimum "
            "is below the lower threshold contributes 10 seconds; not exact time below threshold."
        ),
    )
    flagged_interval_seconds_above_upper_threshold: float = Field(
        default=0.0,
        description=(
            "10-second interval exposure proxy/upper bound: each interval whose source maximum "
            "is above the upper threshold contributes 10 seconds; not exact time above threshold."
        ),
    )
    observations_below_lower_threshold: int = 0
    observations_above_upper_threshold: int = 0
    methodology: str
    metadata: DatasetMetadata


__all__ = [
    "CongestionAnalysisRequest",
    "CongestionAnalysisResponse",
    "CongestionAnalysisResult",
    "ConstrainedVolumeInterval",
    "EeaEventRecord",
    "FrequencyAnalysisRequest",
    "FrequencyAnalysisResponse",
    "OrDirectiveRecord",
    "ParticipantConcentrationRequest",
    "ParticipantConcentrationResponse",
    "ParticipantConcentrationResult",
    "PlanningAreaLoadGenerationInterval",
    "PoolParticipantAgent",
    "PoolParticipantRecord",
    "RegionalAnalysisRequest",
    "RegionalAnalysisResponse",
    "RegionalAnalysisResult",
    "ResearchDataRequest",
    "ResearchDataResponse",
    "ResearchDataset",
    "ResearchRecord",
    "ScarcityAnalysisRequest",
    "ScarcityAnalysisResponse",
    "SupplyAdequacyHistoryInterval",
    "SupplyCushionHistoryInterval",
    "SystemFrequencyInterval",
    "TransmissionOutageHistoryRecord",
]
