# SPDX-License-Identifier: MIT
"""Capability-focused provider protocols for AESO datasets."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from aeso_mcp.models.operations import (
    EnergyMeritOrderBlock,
    GenerationCapacityInterval,
    IntertieCapabilityInterval,
    IntertieOutageRecord,
    LoadOutageForecastInterval,
    MeteredVolumeInterval,
    OperatingReserveOfferBlock,
    UnitCommitmentDirective,
)
from aeso_mcp.models.transmission import TransmissionOutageRecord


@runtime_checkable
class ApprovedTransmissionOutageProvider(Protocol):
    """AESO-approved transmission planned outages."""

    async def get_approved_transmission_outages(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[TransmissionOutageRecord], datetime | None, dict[str, str]]:
        """Return approved outages, optional publication time, and provenance."""
        ...


@runtime_checkable
class LongRangeTransmissionOutageProvider(Protocol):
    """Long-range significant transmission outages (may be tentative)."""

    async def get_long_range_transmission_outages(
        self,
    ) -> tuple[list[TransmissionOutageRecord], datetime | None, dict[str, str]]:
        """Return tentative/coordination outages from the current publication."""
        ...


@runtime_checkable
class OperationalReportsProvider(Protocol):
    """Authenticated AESO APIM operational and participant-report datasets."""

    async def get_energy_merit_order(
        self, report_date: date
    ) -> tuple[list[EnergyMeritOrderBlock], dict[str, str]]: ...

    async def get_unit_commitments(
        self, start_date: date, end_date: date
    ) -> tuple[list[UnitCommitmentDirective], dict[str, str]]: ...

    async def get_generation_capacity(
        self, start_date: date, end_date: date
    ) -> tuple[list[GenerationCapacityInterval], dict[str, str]]: ...

    async def get_load_outage_forecast(
        self, start_date: date, end_date: date
    ) -> tuple[list[LoadOutageForecastInterval], dict[str, str]]: ...

    async def get_intertie_capability(
        self,
        start_date: date,
        end_date: date,
        *,
        start_hour_ending: int,
        end_hour_ending: int,
        include_versions: bool,
    ) -> tuple[list[IntertieCapabilityInterval], dict[str, str]]: ...

    async def get_intertie_outages(
        self, start_date: date, end_date: date
    ) -> tuple[list[IntertieOutageRecord], dict[str, str]]: ...

    async def get_metered_volumes(
        self,
        start_date: date,
        end_date: date,
        *,
        asset_ids: list[str],
        pool_participant_ids: list[str],
    ) -> tuple[list[MeteredVolumeInterval], dict[str, str]]: ...

    async def get_operating_reserve_offer_control(
        self, report_date: date
    ) -> tuple[list[OperatingReserveOfferBlock], dict[str, str]]: ...
