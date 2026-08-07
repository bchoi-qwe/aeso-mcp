# SPDX-License-Identifier: MIT
"""Unit tests for GridStatus provider normalization."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from aeso_mcp.config import Settings
from aeso_mcp.providers.gridstatus import GridStatusProvider
from aeso_mcp.timeutil import MARKET_TZ


def _settings() -> Settings:
    return Settings(aeso_api_key="test-key")  # type: ignore[arg-type]


def _provider_with_client(client: MagicMock) -> GridStatusProvider:
    provider = GridStatusProvider(_settings())
    provider._client = client
    return provider


@pytest.mark.asyncio
async def test_parse_pool_prices_from_dataframe() -> None:
    start = datetime(2024, 1, 15, 0, 0, tzinfo=MARKET_TZ)
    df = pd.DataFrame(
        [
            {
                "Interval Start": start,
                "Interval End": start + pd.Timedelta(hours=1),
                "Pool Price": 42.5,
                "Forecast Pool Price": 40.0,
                "Rolling 30 Day Average Pool Price": 55.0,
            }
        ]
    )
    client = MagicMock()
    client.get_pool_price.return_value = df
    provider = _provider_with_client(client)
    intervals, meta = await provider.get_pool_prices(
        start, start + pd.Timedelta(days=1).to_pytimedelta()
    )
    assert len(intervals) == 1
    assert intervals[0].pool_price_cad_per_mwh == 42.5
    assert meta["provider"] == "gridstatus"


@pytest.mark.asyncio
async def test_parse_fuel_mix_and_interchange() -> None:
    observed = datetime(2024, 1, 15, 12, 0, tzinfo=MARKET_TZ)
    fuel_df = pd.DataFrame([{"Time": observed, "Wind": 1000.0, "Solar": 200.0, "Hydro": 50.0}])
    interchange_df = pd.DataFrame(
        [
            {
                "Time": observed,
                "British Columbia": 100.0,
                "Saskatchewan": -20.0,
                "Net Interchange": 80.0,
            }
        ]
    )
    reserves_df = pd.DataFrame(
        [
            {
                "Time": observed,
                "Contingency Reserve Required": 400.0,
                "Dispatched Contingency Reserve Total": 410.0,
                "Dispatched Contingency Reserve Generation": 300.0,
                "Dispatched Contingency Reserve Other": 110.0,
                "Fast Frequency Response Dispatched": 5.0,
                "Fast Frequency Response Offered": 10.0,
                "Long Lead Time Volume": 20.0,
            }
        ]
    )

    client = MagicMock()
    client.get_fuel_mix.return_value = fuel_df
    client.get_interchange.return_value = interchange_df
    client.get_reserves.return_value = reserves_df

    provider = _provider_with_client(client)
    observed_at, components, _ = await provider.get_fuel_mix()
    assert observed_at == observed
    assert {c.fuel_type for c in components} >= {"Wind", "Solar", "Hydro"}

    _, paths, net, _ = await provider.get_interchange()
    assert net == 80.0
    assert any(p.path == "British Columbia" for p in paths)

    _, reserves, _ = await provider.get_reserves()
    assert reserves["contingency_reserve_required_mw"] == 400.0


@pytest.mark.asyncio
async def test_parse_assets_and_empty_frames() -> None:
    client = MagicMock()
    client.get_asset_list.return_value = pd.DataFrame(
        [
            {
                "Asset ID": "ABC1",
                "Asset Name": "Example",
                "Asset Type": "WIND",
                "Operating Status": "ACTIVE",
                "Pool Participant ID": "P1",
                "Pool Participant Name": "Participant",
                "Net To Grid Asset Flag": "Y",
                "Asset Include Storage Flag": "N",
            }
        ]
    )
    client.get_pool_price.return_value = pd.DataFrame()
    provider = _provider_with_client(client)
    assets, _ = await provider.get_assets()
    assert assets[0].asset_id == "ABC1"
    assert assets[0].net_to_grid is True

    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    intervals, _ = await provider.get_pool_prices(
        start, start + pd.Timedelta(days=1).to_pytimedelta()
    )
    assert intervals == []


@pytest.mark.asyncio
async def test_load_and_smp_parsing() -> None:
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    load_df = pd.DataFrame(
        [
            {
                "Interval Start": start,
                "Interval End": start + pd.Timedelta(hours=1),
                "Alberta Internal Load": 9100.0,
            }
        ]
    )
    forecast_df = pd.DataFrame([{"Interval Start": start, "Load Forecast": 9000.0}])
    smp_df = pd.DataFrame(
        [
            {
                "Interval Start": start,
                "Interval End": start + pd.Timedelta(minutes=1),
                "System Marginal Price": 33.0,
            }
        ]
    )
    client = MagicMock()
    client.get_load.return_value = load_df
    client.get_load_forecast.return_value = forecast_df
    client.get_system_marginal_price.return_value = smp_df
    provider = _provider_with_client(client)

    rows, _ = await provider.get_load(
        start, start + pd.Timedelta(days=1).to_pytimedelta(), include_forecast=True
    )
    assert rows[0]["load_mw"] == 9100.0
    assert rows[0]["load_forecast_mw"] == 9000.0

    smp, _ = await provider.get_system_marginal_prices(
        start, start + pd.Timedelta(days=1).to_pytimedelta()
    )
    assert smp[0].system_marginal_price_cad_per_mwh == 33.0


@pytest.mark.asyncio
async def test_error_translation_auth() -> None:
    client = MagicMock()
    client.get_pool_price.side_effect = Exception("401 Access denied")
    provider = _provider_with_client(client)
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    from aeso_mcp.errors import AuthenticationError

    with pytest.raises(AuthenticationError):
        await provider.get_pool_prices(start, start + pd.Timedelta(days=1).to_pytimedelta())


@pytest.mark.asyncio
async def test_generation_history_auth_is_not_swallowed() -> None:
    client = MagicMock()
    client.get_wind_hourly.side_effect = Exception("403 Access denied")
    client.get_solar_hourly.return_value = pd.DataFrame()
    provider = _provider_with_client(client)
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    from aeso_mcp.errors import AuthenticationError

    with pytest.raises(AuthenticationError):
        await provider.get_generation_history(start, start + pd.Timedelta(days=1).to_pytimedelta())


@pytest.mark.asyncio
async def test_generation_history_raises_when_all_fuels_fail() -> None:
    client = MagicMock()
    client.get_wind_hourly.side_effect = Exception("503 upstream")
    client.get_solar_hourly.side_effect = Exception("503 upstream")
    provider = _provider_with_client(client)
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    from aeso_mcp.errors import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        await provider.get_generation_history(start, start + pd.Timedelta(days=1).to_pytimedelta())


@pytest.mark.asyncio
async def test_generation_history_keeps_partial_fuel_series() -> None:
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    solar_df = pd.DataFrame(
        [
            {
                "Interval Start": start,
                "Interval End": start + pd.Timedelta(hours=1),
                "Solar": 120.0,
            }
        ]
    )
    client = MagicMock()
    client.get_wind_hourly.side_effect = Exception("503 upstream")
    client.get_solar_hourly.return_value = solar_df
    provider = _provider_with_client(client)
    intervals, _ = await provider.get_generation_history(
        start, start + pd.Timedelta(days=1).to_pytimedelta()
    )
    assert len(intervals) == 1
    assert intervals[0].fuel_type == "Solar"
    assert intervals[0].generation_mw == 120.0


@pytest.mark.asyncio
async def test_parse_outages_dataframe() -> None:
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    df = pd.DataFrame(
        [
            {
                "Interval Start": start,
                "Interval End": start + pd.Timedelta(hours=1),
                "Asset ID": "WND1",
                "Asset Name": "Wind One",
                "Fuel Type": "Wind",
                "Outage": 50.0,
                "Maximum Capability": 100.0,
            }
        ]
    )
    client = MagicMock()
    client.get_generator_outages_hourly.return_value = df
    provider = _provider_with_client(client)
    outages, meta = await provider.get_outages(start, start + pd.Timedelta(days=1).to_pytimedelta())
    assert meta["provider"] == "gridstatus"
    assert len(outages) == 1
    assert outages[0].asset_id == "WND1"
    assert outages[0].outage_mw == 50.0
    assert outages[0].interval_start.tzinfo is not None


def test_client_lazy_init_requires_gridstatus() -> None:
    provider = GridStatusProvider(_settings())
    with patch("gridstatus.AESO") as aeso_cls:
        aeso_cls.return_value = MagicMock()
        client = provider._get_client()
        assert client is not None
        aeso_cls.assert_called_once()
