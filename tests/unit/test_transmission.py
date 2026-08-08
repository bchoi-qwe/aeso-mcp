# SPDX-License-Identifier: MIT
"""Unit tests for transmission outage parsing and service semantics."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from aeso_mcp.config import Settings
from aeso_mcp.models.transmission import (
    ApprovedTransmissionOutagesRequest,
    LongRangeTransmissionOutagesRequest,
    TransmissionOutageRecord,
)
from aeso_mcp.providers.gridstatus import GridStatusProvider, _parse_transmission_outages
from aeso_mcp.services.transmission import TransmissionService
from aeso_mcp.timeutil import MARKET_TZ


def _settings() -> Settings:
    return Settings(aeso_api_key="test-key")  # type: ignore[arg-type]


def test_parse_approved_transmission_outages_dataframe() -> None:
    start = datetime(2026, 8, 1, 8, 0, tzinfo=MARKET_TZ)
    end = datetime(2026, 8, 5, 17, 0, tzinfo=MARKET_TZ)
    pub = datetime(2026, 8, 5, 15, 11, tzinfo=MARKET_TZ)
    df = pd.DataFrame(
        [
            {
                "Interval Start": start,
                "Interval End": end,
                "Publish Time": pub,
                "Transmission Owner": "ALTALINK",
                "Type": "Outage",
                "Element": "256s C1",
                "Scheduled Activity": "forced outage",
                "Date Time Comments": "12,129 Hours Continuous",
                "Interconnection": "",
            }
        ]
    )
    records, publication_time = _parse_transmission_outages(df, approval_status="approved")
    assert publication_time == pub
    assert len(records) == 1
    assert records[0].approval_status == "approved"
    assert records[0].element == "256s C1"
    assert records[0].transmission_owner == "ALTALINK"


@pytest.mark.asyncio
async def test_approved_transmission_outages_latest_via_gridstatus() -> None:
    start = datetime(2026, 8, 1, 8, 0, tzinfo=MARKET_TZ)
    pub = datetime(2026, 8, 5, 15, 11, tzinfo=MARKET_TZ)
    df = pd.DataFrame(
        [
            {
                "Interval Start": start,
                "Interval End": start + timedelta(days=1),
                "Publish Time": pub,
                "Transmission Owner": "ATCO",
                "Type": "Outage",
                "Element": "826s 504R",
                "Scheduled Activity": "emergency removal",
                "Date Time Comments": "note",
                "Interconnection": None,
            }
        ]
    )
    client = MagicMock()
    client.get_transmission_outages.return_value = df
    provider = GridStatusProvider(_settings())
    provider._client = client
    records, publication_time, meta = await provider.get_approved_transmission_outages()
    assert meta["provider"] == "gridstatus"
    assert publication_time == pub
    assert records[0].approval_status == "approved"
    client.get_transmission_outages.assert_called_once_with(date="latest")


@pytest.mark.asyncio
async def test_transmission_service_keeps_approval_semantics() -> None:
    approved = AsyncMock()
    long_range = AsyncMock()
    start = datetime(2026, 8, 1, tzinfo=MARKET_TZ)
    approved.get_approved_transmission_outages.return_value = (
        [
            TransmissionOutageRecord(
                interval_start=start,
                interval_end=start + timedelta(days=1),
                element="A",
                approval_status="approved",
            )
        ],
        start,
        {"provider": "gridstatus", "source_product": "Approved"},
    )
    long_range.get_long_range_transmission_outages.return_value = (
        [
            TransmissionOutageRecord(
                interval_start=start,
                interval_end=start + timedelta(days=10),
                element="B",
                approval_status="tentative",
            )
        ],
        start,
        {"provider": "aeso_public_report", "source_product": "Long Range"},
    )
    service = TransmissionService(
        approved_provider=approved,
        long_range_provider=long_range,
        settings=_settings(),
    )
    approved_resp = await service.get_approved_transmission_outages(
        ApprovedTransmissionOutagesRequest()
    )
    long_resp = await service.get_long_range_transmission_outages(
        LongRangeTransmissionOutagesRequest()
    )
    assert approved_resp.approval_status == "approved"
    assert long_resp.approval_status == "tentative"
    assert any("tentative" in w.lower() for w in long_resp.warnings)
