# SPDX-License-Identifier: MIT
"""Tests for cache TTL semantics and CSD single-fetch snapshot parsing."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.providers.gridstatus import GridStatusProvider, _parse_csd_payload
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
    observed_at, data = _parse_csd_payload(payload)
    assert observed_at.tzinfo is not None
    assert data["alberta_internal_load_mw"] == 9500.5
    assert data["total_generation_mw"] == 1500.0
    assert data["net_interchange_mw"] == 150.0
    assert data["reserves"]["contingency_reserve_required_mw"] == 400.0


@pytest.mark.asyncio
async def test_supply_demand_snapshot_single_request() -> None:
    client = MagicMock()
    client._make_request.return_value = {
        "return": {
            "effective_datetime_utc": "2024-01-15T18:30:00.000Z",
            "alberta_internal_load": 9000.0,
            "generation_data_list": [{"fuel_type": "WIND", "aggregated_net_generation": 100.0}],
            "interchange_list": [],
            "contingency_reserve_required": 1.0,
        }
    }
    provider = GridStatusProvider(_settings())
    provider._client = client
    observed_at, payload, meta = await provider.get_supply_demand_snapshot()
    assert observed_at.tzinfo is not None
    assert payload["alberta_internal_load_mw"] == 9000.0
    assert meta["source_product"] == "Current Supply Demand API"
    client._make_request.assert_called_once_with("currentsupplydemand-api/v2/csd/summary/current")
    client.get_fuel_mix.assert_not_called()
    client.get_interchange.assert_not_called()
    client.get_reserves.assert_not_called()
