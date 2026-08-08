# SPDX-License-Identifier: MIT
"""Tests for cache TTL semantics and CSD single-fetch snapshot parsing."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.providers.csd import parse_csd_payload
from aeso_mcp.providers.gridstatus import GridStatusProvider
from aeso_mcp.services.ttl import historical_ttl_s
from aeso_mcp.timeutil import MARKET_TZ, as_market_date, market_now, start_of_market_day


def _settings() -> Settings:
    return Settings(
        aeso_api_key="test-key",
        cache_ttl_snapshot_s=30.0,
        cache_ttl_historical_s=86_400.0,
    )  # type: ignore[arg-type]


def test_ttl_uses_short_for_ranges_overlapping_today() -> None:
    settings = _settings()
    today = as_market_date(market_now())
    start = start_of_market_day(today)
    end = start + timedelta(hours=12)
    assert historical_ttl_s(settings, start, end) == settings.cache_ttl_snapshot_s


def test_ttl_uses_long_for_completed_past_days() -> None:
    settings = _settings()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 3, tzinfo=MARKET_TZ)
    assert historical_ttl_s(settings, start, end) == settings.cache_ttl_historical_s


def test_parse_csd_payload_extracts_ail_and_mix() -> None:
    payload = {
        "return": {
            "effective_datetime_utc": "2024-01-15T18:30:00.000Z",
            "alberta_internal_load": 9500.5,
            "generation_data_list": [
                {
                    "fuel_type": "WIND",
                    "aggregated_net_generation": 1200.0,
                    "aggregated_maximum_capability": 2000.0,
                },
                {
                    "fuel_type": "SOLAR",
                    "aggregated_net_generation": 300.0,
                    "aggregated_maximum_capability": 800.0,
                },
            ],
            "interchange_list": [
                {"path": "British Columbia", "actual_flow": 200.0},
                {"path": "Saskatchewan", "actual_flow": -50.0},
            ],
            "contingency_reserve_required": 400.0,
            "dispatched_contigency_reserve_total": 420.0,
        }
    }
    observed_at, data = parse_csd_payload(payload)
    assert observed_at.tzinfo is not None
    assert data["alberta_internal_load_mw"] == 9500.5
    assert data["total_generation_mw"] == 1500.0
    assert data["net_interchange_mw"] == 150.0
    assert data["reserves"]["contingency_reserve_required_mw"] == 400.0


@pytest.mark.asyncio
async def test_supply_demand_snapshot_single_request() -> None:
    from unittest.mock import AsyncMock

    apim_http = AsyncMock()
    apim_http.get_json.return_value = {
        "return": {
            "effective_datetime_utc": "2024-01-15T18:30:00.000Z",
            "alberta_internal_load": 9000.0,
            "generation_data_list": [{"fuel_type": "WIND", "aggregated_net_generation": 100.0}],
            "interchange_list": [],
            "contingency_reserve_required": 1.0,
        }
    }
    provider = GridStatusProvider(_settings(), apim_http=apim_http)
    provider._client = MagicMock()
    observed_at, payload, meta = await provider.get_supply_demand_snapshot()
    assert observed_at.tzinfo is not None
    assert payload["alberta_internal_load_mw"] == 9000.0
    assert meta["source_product"] == "Current Supply Demand API"
    apim_http.get_json.assert_called_once_with("currentsupplydemand-api/v2/csd/summary/current")
    provider._client.get_fuel_mix.assert_not_called()
    provider._client.get_interchange.assert_not_called()
    provider._client.get_reserves.assert_not_called()


@pytest.mark.asyncio
async def test_cache_evicts_when_over_max_entries() -> None:
    from aeso_mcp.services.cache import AsyncTTLCache

    cache = AsyncTTLCache(max_entries=2)

    async def _make(value: int):
        async def factory() -> int:
            return value

        return factory

    await cache.get_or_set("a", await _make(1), ttl_s=60)
    await cache.get_or_set("b", await _make(2), ttl_s=60)
    assert len(cache) == 2
    await cache.get_or_set("c", await _make(3), ttl_s=60)
    assert len(cache) == 2
    assert "c" in {k for k in ("a", "b", "c") if k in cache._store}


@pytest.mark.asyncio
async def test_snapshot_auth_failure_is_not_swallowed() -> None:
    from unittest.mock import AsyncMock

    from aeso_mcp.errors import AuthenticationError
    from aeso_mcp.models.generation import FuelMixComponent
    from aeso_mcp.services.market import MarketService

    provider = AsyncMock()
    provider.get_supply_demand_snapshot.return_value = (
        datetime(2024, 1, 15, 12, 0, tzinfo=MARKET_TZ),
        {
            "generation_by_fuel": [FuelMixComponent(fuel_type="Wind", generation_mw=100.0)],
            "reserves": {},
            "alberta_internal_load_mw": 9000.0,
            "total_generation_mw": 100.0,
            "net_interchange_mw": 0.0,
            "interchange_paths": [],
        },
        {"provider": "gridstatus", "source_product": "CSD"},
    )
    provider.get_pool_prices.side_effect = AuthenticationError("bad key")
    service = MarketService(provider, _settings())
    with pytest.raises(AuthenticationError):
        await service.get_market_snapshot()


@pytest.mark.asyncio
async def test_snapshot_marks_preliminary_when_price_missing() -> None:
    from unittest.mock import AsyncMock

    from aeso_mcp.models.common import DataStatus
    from aeso_mcp.models.generation import FuelMixComponent
    from aeso_mcp.services.market import MarketService

    provider = AsyncMock()
    provider.get_supply_demand_snapshot.return_value = (
        datetime(2024, 1, 15, 12, 0, tzinfo=MARKET_TZ),
        {
            "generation_by_fuel": [FuelMixComponent(fuel_type="Wind", generation_mw=100.0)],
            "reserves": {},
            "alberta_internal_load_mw": 9000.0,
            "total_generation_mw": 100.0,
            "net_interchange_mw": 0.0,
            "interchange_paths": [],
        },
        {"provider": "gridstatus", "source_product": "CSD"},
    )
    provider.get_pool_prices.return_value = (
        [],
        {"provider": "gridstatus", "source_product": "Pool Price API"},
    )
    provider.get_system_marginal_prices.return_value = (
        [],
        {"provider": "gridstatus", "source_product": "SMP"},
    )
    service = MarketService(provider, _settings())
    snap = await service.get_market_snapshot()
    assert snap.metadata.status == DataStatus.PRELIMINARY
    assert any("pool price missing" in w.lower() for w in snap.warnings)
