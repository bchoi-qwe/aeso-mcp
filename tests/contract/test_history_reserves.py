# SPDX-License-Identifier: MIT
"""Contract tests for historical CSD and public reserve report normalization."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.providers.historical_generation import HistoricalGenerationProvider
from aeso_mcp.providers.public_reports import AesoPublicReportsProvider


def _box_html() -> str:
    payload = {
        "/app-api/enduserapp/shared-folder": {
            "items": [
                {
                    "type": "file",
                    "id": 123,
                    "name": "CSD Generation (Hourly) - 2024-11.zip",
                    "itemSize": 500,
                    "contentUpdated": 1730505600,
                    "fileIDHash": "abc123",
                }
            ],
            "pageCount": 1,
        }
    }
    return f"<script>Box.postStreamData = {json.dumps(payload)};</script>"


def _generation_zip() -> bytes:
    csv_body = """Date (MST),Date (MPT),Asset Short Name,Asset Name,Asset Grouping,Volume,Maximum Capability,System Capability,Fuel Type,Sub Fuel Type,Planning Area,Region
2024-11-03 01:00:00,2024-11-03 01:00:00,TEST1,Test Asset,Gas,100.5,120,115,GAS,COMBINED CYCLE,1,SOUTH
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("generation.csv", csv_body)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_historical_generation_catalog_zip_and_dst_instant() -> None:
    http = AsyncMock()
    http.get_text.return_value = _box_html()
    http.get_bytes.return_value = _generation_zip()
    provider = HistoricalGenerationProvider(http)

    sources = await provider.list_source_files("hourly")
    assert len(sources) == 1
    records, digest = await provider.fetch_source_file(
        sources[0],
        interval="hourly",
        start=datetime(2024, 11, 3, 7, tzinfo=UTC),
        end=datetime(2024, 11, 3, 10, tzinfo=UTC),
    )

    assert len(records) == 1
    assert records[0].asset_id == "TEST1"
    assert records[0].interval_start_utc == datetime(2024, 11, 3, 8, tzinfo=UTC)
    assert records[0].interval_start.fold == 1
    assert records[0].generation_mw == pytest.approx(100.5)
    assert len(digest) == 64


@pytest.mark.asyncio
async def test_public_operating_reserve_and_uc_contracts() -> None:
    active = b"""Active Operating Reserve Price Report\n\nDate, RR ON PEAK $/MW, RR ON PEAK MW, SR OFF PEAK $/MW, SR OFF PEAK MW\n"08/01/2026","$27.65","210","-$0.51","233"\n"""
    standby = b"""Standby Operating Reserve Price Report\n\nDate, RR ON PEAK Premium Price, RR ON PEAK Activation Price, RR ON PEAK Clearing Blended Price, RR ON PEAK Volume\n"08/01/2026","$23.75","$200.00","$30.95","20"\n"""
    forecast = b"""Seven-day Forecast\n\nDate,HE,Active Regulating,Active Spinning,Active Supplemental,Standby Regulating,Standby Spinning,Standby Supplemental\n"08/24/2026","1","185","233","233","20","65","45"\n"""
    activation = b"""Standby Activation\n\nService Level,Date,HE,Reserve Type,Total Volume Activated (MW),Weighted Average Activation Price ($/MWh)\n"STANDBY","08/01/2026","3","SR","3","6.00"\n"""
    uc = b"""UC Summary Report\n\n"Report Date:","August 23, 2026."\n\nDate,HE,Total UC Amount ($),Total Charged Volume (MW)\n"08/01/2026","01","100.00","25.00"\n\n"Report Date:","August 23, 2026."\n\nDate,HE,Total UC Amount ($),Total Charged Volume (MW)\n"08/02/2026","02","125.00","30.00"\n"""

    async def get_bytes(url: str) -> bytes:
        if "ActiveOperating" in url:
            return active
        if "StandbyOperating" in url:
            return standby
        if "ASPForecast" in url:
            return forecast
        if "ASPActivation" in url:
            return activation
        return uc

    http = AsyncMock()
    http.get_bytes.side_effect = get_bytes
    provider = AesoPublicReportsProvider(http)

    prices, _, _ = await provider.get_operating_reserve_prices(
        datetime(2026, 8, 1).date(), datetime(2026, 8, 1).date()
    )
    assert len(prices) == 3
    assert next(
        item for item in prices if item.procurement == "active"
    ).active_price_cad_per_mw == pytest.approx(27.65)
    standby_row = next(item for item in prices if item.procurement == "standby")
    assert standby_row.clearing_blended_price_cad_per_mw == pytest.approx(30.95)
    assert standby_row.activation_price_cad_per_mwh == pytest.approx(200.0)

    forecasts, _, _ = await provider.get_operating_reserve_forecast()
    assert forecasts[0].active_regulating_mw == 185
    activations, _, _ = await provider.get_operating_reserve_activations(
        datetime(2026, 8, 1).date(), datetime(2026, 8, 1).date()
    )
    assert activations[0].reserve_type == "spinning"
    assert activations[0].activated_volume_mw == 3

    settlements, report_time, _ = await provider.get_unit_commitment_settlement_summary(
        datetime(2026, 8, 1).date(), datetime(2026, 8, 1).date()
    )
    assert len(settlements) == 2
    assert settlements[0].total_uc_amount_cad == 100
    assert settlements[1].total_uc_amount_cad == 125
    assert report_time is not None and report_time.date().isoformat() == "2026-08-23"
