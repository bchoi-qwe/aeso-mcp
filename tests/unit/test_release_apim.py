# SPDX-License-Identifier: MIT
"""Boundary and normalization coverage for the direct APIM provider."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.errors import DataValidationError
from aeso_mcp.models.common import ProviderName
from aeso_mcp.providers import aeso_apim
from aeso_mcp.providers.aeso_apim import AesoApimProvider
from aeso_mcp.timeutil import MARKET_TZ


def _provider() -> tuple[AesoApimProvider, AsyncMock]:
    http = AsyncMock()
    return AesoApimProvider(http), http


@pytest.mark.asyncio
async def test_apim_rejects_non_list_price_reports() -> None:
    provider, http = _provider()
    http.get_json.return_value = {"return": {"Pool Price Report": {"row": 1}}}

    with pytest.raises(DataValidationError, match="Pool Price Report shape"):
        await provider.get_pool_prices(
            datetime(2024, 1, 1, tzinfo=MARKET_TZ),
            datetime(2024, 1, 2, tzinfo=MARKET_TZ),
        )


@pytest.mark.asyncio
async def test_apim_normalizes_and_filters_smp_rows() -> None:
    provider, http = _provider()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    http.get_json.return_value = {
        "return": {
            "System Marginal Price Report": [
                "not a row",
                {"begin_datetime_utc": "2024-01-01T07:00:00Z"},
                {
                    "begin_datetime_utc": "2024-01-01T08:00:00Z",
                    "system_marginal_price": "42.5",
                },
                {
                    "begin_datetime_utc": "2024-01-02T08:00:00Z",
                    "system_marginal_price": 99,
                    "end_datetime_utc": "2024-01-02T08:01:00Z",
                },
            ]
        }
    }

    intervals, metadata = await provider.get_system_marginal_prices(
        start, start + timedelta(hours=2)
    )

    assert len(intervals) == 1
    assert intervals[0].system_marginal_price_cad_per_mwh == 42.5
    assert intervals[0].interval_end == datetime(2024, 1, 1, 1, 1, tzinfo=MARKET_TZ)
    assert metadata == {
        "provider": ProviderName.AESO_APIM.value,
        "source_product": "System Marginal Price API",
        "api_version": "v1.1",
    }


@pytest.mark.asyncio
async def test_apim_load_supports_nested_aliases_and_forecast() -> None:
    provider, http = _provider()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    http.get_json.return_value = {
        "return": {
            "load": [
                "not a row",
                {
                    "beginDateTimeUTC": "2024-01-01T07:00:00Z",
                    "Alberta Internal Load": "9000",
                    "alberta_internal_load_forecast": "9100",
                },
                {
                    "begin_datetime_utc": "2024-01-02T07:00:00Z",
                    "alberta_internal_load": 8000,
                },
            ]
        }
    }

    rows, metadata = await provider.get_load(
        start, start + timedelta(hours=2), include_forecast=True
    )

    assert len(rows) == 1
    assert rows[0]["load_mw"] == 9000.0
    assert rows[0]["load_forecast_mw"] == 9100.0
    assert metadata["source_product"] == "Alberta Internal Load API"


@pytest.mark.asyncio
async def test_apim_rejects_invalid_load_and_asset_shapes() -> None:
    provider, http = _provider()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    end = start + timedelta(hours=1)

    http.get_json.return_value = {"return": {"Actual Forecast Report": "invalid"}}
    with pytest.raises(DataValidationError, match="load response shape"):
        await provider.get_load(start, end)

    http.get_json.return_value = {"return": {"Asset List": "invalid"}}
    with pytest.raises(DataValidationError, match="asset list shape"):
        await provider.get_assets()


@pytest.mark.asyncio
async def test_apim_assets_pass_all_filters_and_normalize_aliases() -> None:
    provider, http = _provider()
    http.get_json.return_value = {
        "return": {
            "assets": [
                {
                    "asset_id": " A1 ",
                    "asset_name": " Wind One ",
                    "asset_type": "Wind",
                    "operating_status": "Active",
                    "pool_participant_ID": " P1 ",
                    "pool_participant_name": " Participant ",
                },
                {"asset_ID": "A2", "asset_name": "", "pool_participant_name": None},
                {},
                "not a mapping",
            ]
        }
    }

    assets, metadata = await provider.get_assets(
        asset_id="A1",
        pool_participant_id="P1",
        operating_status="Active",
        asset_type="Wind",
    )

    assert [asset.asset_id for asset in assets] == [" A1 ", "A2"]
    assert assets[0].asset_name == "Wind One"
    assert assets[0].pool_participant_id == "P1"
    assert assets[1].asset_name is None
    assert metadata["source_product"] == "Asset List API"
    http.get_json.assert_awaited_once_with(
        "assetlist-api/v1/assetlist",
        params={
            "asset_ID": "A1",
            "pool_participant_ID": "P1",
            "operating_status": "Active",
            "asset_type": "Wind",
        },
    )


@pytest.mark.asyncio
async def test_apim_csd_accessors_validate_payload_shapes(monkeypatch) -> None:
    provider, http = _provider()
    observed_at = datetime(2024, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(
        aeso_apim,
        "parse_csd_payload",
        lambda _: (observed_at, {"generation_by_fuel": {}}),
    )
    http.get_json.return_value = {}
    with pytest.raises(DataValidationError, match="generation_by_fuel"):
        await provider.get_fuel_mix()

    monkeypatch.setattr(
        aeso_apim,
        "parse_csd_payload",
        lambda _: (observed_at, {"interchange_paths": {}, "net_interchange_mw": "bad"}),
    )
    with pytest.raises(DataValidationError, match="interchange_paths"):
        await provider.get_interchange()

    monkeypatch.setattr(
        aeso_apim,
        "parse_csd_payload",
        lambda _: (observed_at, {"interchange_paths": [], "net_interchange_mw": "bad"}),
    )
    with pytest.raises(DataValidationError, match="net_interchange_mw"):
        await provider.get_interchange()

    monkeypatch.setattr(
        aeso_apim,
        "parse_csd_payload",
        lambda _: (observed_at, {"reserves": []}),
    )
    with pytest.raises(DataValidationError, match="reserves"):
        await provider.get_reserves()


@pytest.mark.asyncio
async def test_apim_snapshot_returns_normalized_csd_payload(monkeypatch) -> None:
    provider, _http = _provider()
    observed_at = datetime(2024, 1, 1, tzinfo=UTC)
    payload = {
        "generation_by_fuel": [],
        "interchange_paths": [],
        "net_interchange_mw": 0,
        "reserves": {},
    }
    monkeypatch.setattr(aeso_apim, "parse_csd_payload", lambda _: (observed_at, payload))

    observed, returned, metadata = await provider.get_supply_demand_snapshot()

    assert observed == observed_at
    assert returned == payload
    assert metadata["provider"] == ProviderName.AESO_APIM.value
    assert metadata["api_version"] == "v2"
