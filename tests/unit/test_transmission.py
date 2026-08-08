# SPDX-License-Identifier: MIT
"""Unit tests for transmission outage parsing and service semantics."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from aeso_mcp.config import Settings
from aeso_mcp.models.transmission import (
    ApprovedTransmissionOutagesRequest,
    LongRangeTransmissionOutagesRequest,
    TransmissionOutageRecord,
)
from aeso_mcp.providers.public_reports import (
    APPROVED_TX_LANDING_URL,
    AesoPublicReportsProvider,
)
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient
from aeso_mcp.services.transmission import TransmissionService
from aeso_mcp.timeutil import MARKET_TZ


def _settings() -> Settings:
    return Settings(aeso_api_key="test-key")  # type: ignore[arg-type]


@respx.mock
@pytest.mark.asyncio
async def test_approved_transmission_outages_latest_via_public_client() -> None:
    html = (
        "<html><body>"
        '<a href="csvData\\\\_2026-08-05_15-11-00_qryOpPlanTransmissionTable_1.csv">'
        "CSV</a>"
        "</body></html>"
    )
    csv_body = (
        b"Owner,From,To,Duration,Type,Element,Scheduled Activity,"
        b"Date/Time Comments,Interconnection\n"
        b"ATCO,01-Aug-26 08:00,02-Aug-26 08:00,24 Hours,Outage,826s 504R,"
        b"emergency removal,note,\n"
    )
    respx.get(APPROVED_TX_LANDING_URL).mock(return_value=httpx.Response(200, text=html))
    respx.get(url__regex=r".*csvData/.*qryOpPlanTransmissionTable_1\.csv").mock(
        return_value=httpx.Response(200, content=csv_body)
    )
    client = AesoPublicReportsHttpClient(_settings())
    try:
        provider = AesoPublicReportsProvider(client)
        records, publication_time, meta = await provider.get_approved_transmission_outages()
    finally:
        await client.aclose()
    assert meta["provider"] == "aeso_public_report"
    assert publication_time is not None
    assert publication_time.year == 2026
    assert records[0].approval_status == "approved"
    assert records[0].element == "826s 504R"


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
        {"provider": "aeso_public_report", "source_product": "Approved"},
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
    assert approved_resp.metadata.provider.value == "aeso_public_report"
