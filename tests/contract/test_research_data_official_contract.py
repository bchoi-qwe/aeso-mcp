# SPDX-License-Identifier: MIT
"""Contract-shape tests for the verified AESO research-data publications."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import aeso_mcp.providers.research_data as research_data_provider
from aeso_mcp.config import Settings
from aeso_mcp.errors import DataValidationError, InvalidDateRangeError
from aeso_mcp.providers.http import AesoHttpClient
from aeso_mcp.providers.operations import AesoOperationsProvider
from aeso_mcp.providers.public_reports_http import (
    _MAX_PUBLIC_ASSET_BYTES,
    AesoPublicReportsHttpClient,
)
from aeso_mcp.providers.research_data import (
    _MAX_ARCHIVE_MEMBER_BYTES,
    CONSTRAINED_VOLUME_URL,
    EEA_EVENTS_URL,
    HISTORICAL_SUPPLY_ADEQUACY_URL,
    HISTORICAL_SUPPLY_CUSHION_URL,
    HISTORICAL_TRANSMISSION_OUTAGES_URL,
    PLANNING_AREA_URL_TEMPLATE,
    SYSTEM_FREQUENCY_URLS,
    AesoResearchDataProvider,
    _parse_or_directives_xlsx,
    _validate_archive_member,
)
from aeso_mcp.timeutil import MARKET_TZ

FIXTURES = Path(__file__).parent / "fixtures"


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"aeso_api_key": "test-key", **overrides})


def _xlsx_fixture(name: str) -> bytes:
    xml = (FIXTURES / name).read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", xml)
    return output.getvalue()


def _csv_zip_fixture(name: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("frequency.csv", (FIXTURES / name).read_bytes())
    return output.getvalue()


def _research_provider(payloads: dict[str, bytes]) -> AesoResearchDataProvider:
    http = AsyncMock()
    http.get_binary_asset.side_effect = lambda url: payloads[url]
    return AesoResearchDataProvider(cast(AesoPublicReportsHttpClient, http))


@pytest.mark.asyncio
async def test_official_web_code_contracts_preserve_categorical_codes() -> None:
    provider = _research_provider(
        {
            HISTORICAL_SUPPLY_ADEQUACY_URL: _xlsx_fixture(
                "research_data_official_supply_adequacy_sheet.xml"
            ),
            HISTORICAL_SUPPLY_CUSHION_URL: _xlsx_fixture(
                "research_data_official_supply_cushion_sheet.xml"
            ),
        }
    )
    adequacy, adequacy_provenance = await provider.get_supply_adequacy_history()
    cushion, cushion_provenance = await provider.get_supply_cushion_history()

    assert [row.web_code for row in adequacy] == [4, 5]
    assert adequacy[0].interval_start.isoformat().startswith("2024-01-01T00:00:00")
    assert cushion[0].web_code == 7
    assert cushion[0].version_start.hour == 10
    assert cast(str, adequacy_provenance["source_file_name"]).endswith(".xlsx")
    assert cushion_provenance["source_publication_date"] == "2026-03-04"


@pytest.mark.asyncio
async def test_official_transmission_contract_allows_repeated_source_keys() -> None:
    provider = _research_provider(
        {
            HISTORICAL_TRANSMISSION_OUTAGES_URL: _xlsx_fixture(
                "research_data_official_transmission_sheet.xml"
            )
        }
    )
    rows, provenance = await provider.get_transmission_outage_history()

    assert len(rows) == 2
    assert rows[0].activity == "Maintenance"
    assert rows[1].outage_condition == "Contingency"
    assert "source_unbounded_rows" not in provenance


@pytest.mark.asyncio
async def test_official_planning_contract_preserves_blank_numeric_fields_and_dst() -> None:
    url = PLANNING_AREA_URL_TEMPLATE.format(year=2024)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "Planning Area Load - Hourly - 2024.csv",
            (FIXTURES / "research_data_official_planning.csv").read_bytes(),
        )
    provider = _research_provider({url: output.getvalue()})
    rows, _ = await provider.get_planning_area_history(
        start=datetime(2024, 1, 1, tzinfo=MARKET_TZ),
        end=datetime(2024, 1, 1, 1, tzinfo=MARKET_TZ),
    )

    assert len(rows) == 1
    assert rows[0].system_generation_mw is None
    assert rows[0].csd_generation_mw == 0
    assert rows[0].interval_start.hour == 0


@pytest.mark.asyncio
async def test_official_constrained_contract_keeps_same_hour_by_fuel_and_area() -> None:
    provider = _research_provider(
        {CONSTRAINED_VOLUME_URL: (FIXTURES / "research_data_official_constrained.csv").read_bytes()}
    )
    rows, _ = await provider.get_constrained_volume_history(
        start=datetime(2024, 1, 1, tzinfo=MARKET_TZ),
        end=datetime(2024, 1, 1, 2, tzinfo=MARKET_TZ),
    )

    assert len(rows) == 3
    assert len({row.interval_start for row in rows[:2]}) == 1
    assert {row.fuel_type for row in rows[:2]} == {"THERMAL", "WIND"}


@pytest.mark.asyncio
async def test_official_eea_contract_preserves_alert_level_and_comments() -> None:
    provider = _research_provider(
        {EEA_EVENTS_URL: _xlsx_fixture("research_data_official_eea_sheet.xml")}
    )
    rows, provenance = await provider.get_eea_event_history()

    assert rows[0].eea_level == "EEA1"
    assert rows[0].duration_minutes == 60
    assert rows[0].comments == "Test event"
    assert provenance["source_publication_date"] == "2024-11-21"


@pytest.mark.asyncio
async def test_official_frequency_contract_accepts_zip_and_enforces_six_hour_bound() -> None:
    url = SYSTEM_FREQUENCY_URLS[2024]
    provider = _research_provider({url: _csv_zip_fixture("research_data_official_frequency.csv")})
    rows, provenance = await provider.get_system_frequency_history(
        start=datetime(2024, 1, 1, tzinfo=MARKET_TZ),
        end=datetime(2024, 1, 1, 0, 1, tzinfo=MARKET_TZ),
    )

    assert len(rows) == 2
    assert rows[0].minimum_frequency_hz == 59.94
    assert provenance["frequency_resolution"] == "10s"
    with pytest.raises(InvalidDateRangeError, match="6 hours"):
        await provider.get_system_frequency_history(
            start=datetime(2024, 1, 1, tzinfo=MARKET_TZ),
            end=datetime(2024, 1, 1, 7, tzinfo=MARKET_TZ),
        )


def test_official_or_directive_contract_preserves_event_fields() -> None:
    rows = _parse_or_directives_xlsx(
        _xlsx_fixture("research_data_official_or_directives_sheet.xml"),
        start=None,
        end=None,
    )

    assert rows[0].event_number == 1
    assert rows[0].service == "SPIN"
    assert rows[0].event_duration_seconds == 60
    assert rows[0].energy_mwh == 1


def test_official_or_directive_contract_preserves_legacy_capacity_semantics() -> None:
    rows = _parse_or_directives_xlsx(
        _xlsx_fixture("research_data_official_or_directives_legacy_capacity_sheet.xml"),
        start=None,
        end=None,
    )

    assert rows[0].capacity_mw == 238
    assert rows[0].time_weighted_average_capacity_mw is None


@pytest.mark.asyncio
async def test_official_or_directive_history_deduplicates_overlapping_asset_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.aeso.ca/assets/Uploads/data-requests/or-duplicate.xlsx"
    monkeypatch.setattr(research_data_provider, "OR_DIRECTIVES_URLS", (url, url))
    provider = _research_provider(
        {url: _xlsx_fixture("research_data_official_or_directives_sheet.xml")}
    )

    rows, _ = await provider.get_or_directive_history()

    assert len(rows) == 1


@pytest.mark.asyncio
async def test_pool_participant_apim_contract_uses_verified_path_and_filters() -> None:
    payload = json.loads(
        (FIXTURES / "research_data_official_pool_participants.json").read_text(encoding="utf-8")
    )
    http = AsyncMock()
    http.get_json.return_value = payload
    provider = AesoOperationsProvider(cast(AesoHttpClient, http))

    rows, provenance = await provider.get_pool_participants(
        pool_participant_ids=["PP1", "PP2"],
        pool_participant_name="Participant One",
    )

    assert rows[0].pool_participant_id == "PP1"
    assert rows[0].agents[0].agent_id == "AG1"
    assert provenance["api_version"] == "v1"
    http.get_json.assert_awaited_once_with(
        "poolparticipant-api/v1/poolparticipantlist",
        params={
            "pool_participant_ID": "PP1,PP2",
            "pool_participant_name": "Participant One",
        },
    )


@pytest.mark.asyncio
async def test_pool_participant_apim_rejects_mixed_row_shapes() -> None:
    http = AsyncMock()
    http.get_json.return_value = {
        "return": {
            "pool_participant_list": [
                {
                    "pool_participant_ID": "PP1",
                    "pool_participant_name": "Participant One",
                },
                "unexpected-row",
            ]
        }
    }
    provider = AesoOperationsProvider(cast(AesoHttpClient, http))

    with pytest.raises(DataValidationError, match="non-object row"):
        await provider.get_pool_participants(
            pool_participant_ids=[],
            pool_participant_name=None,
        )


@respx.mock
@pytest.mark.asyncio
async def test_public_binary_client_restricts_fixed_official_asset_boundary() -> None:
    url = HISTORICAL_SUPPLY_ADEQUACY_URL
    respx.get(url).mock(return_value=httpx.Response(200, content=b"PK\x03\x04archive"))
    client = AesoPublicReportsHttpClient(_settings())
    try:
        assert await client.get_binary_asset(url) == b"PK\x03\x04archive"
        with pytest.raises(DataValidationError, match="fixed files"):
            await client.get_binary_asset("https://aeso.ca/assets/Uploads/file.xlsx")
        with pytest.raises(DataValidationError, match="static file"):
            await client.get_binary_asset(url + "?download=1")
    finally:
        await client.aclose()


def test_public_binary_asset_bound_covers_verified_frequency_file_with_margin() -> None:
    assert _MAX_PUBLIC_ASSET_BYTES == 192 * 1024 * 1024


def test_archive_member_expansion_is_bounded() -> None:
    info = zipfile.ZipInfo("frequency.csv")
    info.file_size = _MAX_ARCHIVE_MEMBER_BYTES + 1

    with pytest.raises(DataValidationError, match="uncompressed safety limit"):
        _validate_archive_member(info, "System Frequency")


@respx.mock
@pytest.mark.asyncio
async def test_public_binary_client_revalidates_redirect_target() -> None:
    url = HISTORICAL_SUPPLY_ADEQUACY_URL
    respx.get(url).mock(
        return_value=httpx.Response(302, headers={"Location": "https://aeso.ca/report.csv"})
    )
    respx.get("https://aeso.ca/report.csv").mock(return_value=httpx.Response(200, content=b"csv"))
    client = AesoPublicReportsHttpClient(_settings())
    try:
        with pytest.raises(DataValidationError, match="fixed files"):
            await client.get_binary_asset(url)
    finally:
        await client.aclose()
