# SPDX-License-Identifier: MIT
"""Transmission outage domain models (distinct from generator outages)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aeso_mcp.models.common import DatasetMetadata, WarningMixin

ApprovalStatus = Literal["approved", "tentative"]


class TransmissionOutageRecord(BaseModel):
    """One transmission facility outage observation."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime | None = None
    publication_time: datetime | None = None
    transmission_owner: str | None = None
    element_type: str | None = None
    element: str
    scheduled_activity: str | None = None
    comments: str | None = None
    interconnection: str | None = None
    approval_status: ApprovalStatus
    duration_note: str | None = None


class ApprovedTransmissionOutagesRequest(BaseModel):
    """Request approved transmission planned outages.

    Omit ``start``/``end`` for the current AESO publication. When provided, the
    range selects historical *publication* windows (not outage intervals) and is
    tightly bounded because upstream navigation walks archive pages.
    """

    model_config = ConfigDict(extra="forbid")

    start: datetime | None = Field(
        default=None,
        description=(
            "Optional inclusive start for historical publication lookup "
            "(America/Edmonton if naive). Omit with end for the latest report."
        ),
    )
    end: datetime | None = Field(
        default=None,
        description="Optional exclusive end for historical publication lookup.",
    )


class LongRangeTransmissionOutagesRequest(BaseModel):
    """Request the current Long Range Significant Transmission Outages publication."""

    model_config = ConfigDict(extra="forbid")

    # Current publication only; historical archive navigation is not implemented.
    include_tentative_only: Literal[True] = Field(
        default=True,
        description=(
            "Long-range reports are tentative/coordination listings. "
            "Only the current publication is supported."
        ),
    )


class TransmissionOutagesResponse(WarningMixin):
    """Transmission outage observations from a single publication semantics."""

    outages: list[TransmissionOutageRecord]
    approval_status: ApprovalStatus
    publication_time: datetime | None = None
    metadata: DatasetMetadata
