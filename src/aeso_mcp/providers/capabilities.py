# SPDX-License-Identifier: MIT
"""Capability-focused provider protocols for AESO datasets."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

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
