# SPDX-License-Identifier: MIT
"""Focused tests for response provenance, completeness, and safe errors."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from aeso_mcp.config import Settings
from aeso_mcp.errors import InvalidDateRangeError, UpstreamUnavailableError
from aeso_mcp.mcp.errors import map_errors
from aeso_mcp.models.analytics import FindPriceEventsRequest
from aeso_mcp.models.common import DataCompleteness
from aeso_mcp.models.generation import GenerationRequest
from aeso_mcp.models.prices import PoolPriceInterval, PoolPriceRequest
from aeso_mcp.providers.gridstatus import GridStatusProvider
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import MARKET_TZ


def _settings() -> Settings:
    return Settings(aeso_api_key="test-key")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cache_result_preserves_provider_fetch_time_on_hit() -> None:
    cache = AsyncTTLCache()
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        return "value"

    first = await cache.get_or_set_with_metadata("key", factory, ttl_s=60)
    second = await cache.get_or_set_with_metadata("key", factory, ttl_s=60)

    assert calls == 1
    assert first.value == second.value == "value"
    assert first.info.cache_hit is False
    assert second.info.cache_hit is True
    assert second.info.retrieved_at == first.info.retrieved_at
    assert second.info.served_at >= second.info.retrieved_at
    assert second.info.cache_age is not None
    assert second.info.cache_age >= 0


@pytest.mark.asyncio
async def test_market_metadata_reports_cache_and_expected_observations() -> None:
    provider = AsyncMock()
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    provider.get_pool_prices.return_value = (
        [
            PoolPriceInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                pool_price_cad_per_mwh=42.0,
            )
        ],
        {"provider": "gridstatus", "source_product": "Pool Price API"},
    )
    service = MarketService(provider, _settings())
    request = PoolPriceRequest(start=start, end=start + timedelta(hours=1))

    first = await service.get_pool_prices(request)
    second = await service.get_pool_prices(request)

    assert provider.get_pool_prices.await_count == 1
    assert first.metadata.cache_hit is False
    assert second.metadata.cache_hit is True
    assert second.metadata.retrieved_at == first.metadata.retrieved_at
    assert second.metadata.served_at is not None
    assert second.metadata.cache_age is not None
    assert second.metadata.expected_observations == 1
    assert second.metadata.missing_observations == 0
    assert second.metadata.expected_observation_count == 1
    assert second.metadata.missing_observation_count == 0
    assert second.metadata.completeness == DataCompleteness.COMPLETE


@pytest.mark.asyncio
async def test_pool_prices_page_after_full_fetch_and_sort() -> None:
    provider = AsyncMock()
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    intervals = [
        PoolPriceInterval(
            interval_start=start + timedelta(hours=index),
            interval_end=start + timedelta(hours=index + 1),
            pool_price_cad_per_mwh=float(index),
        )
        for index in range(5)
    ]
    provider.get_pool_prices.return_value = (
        list(reversed(intervals)),
        {"provider": "gridstatus", "source_product": "Pool Price API"},
    )
    service = MarketService(provider, _settings())

    response = await service.get_pool_prices(
        PoolPriceRequest(
            start=start,
            end=start + timedelta(hours=5),
            offset=1,
            limit=2,
        )
    )

    assert [item.pool_price_cad_per_mwh for item in response.intervals] == [1.0, 2.0]
    assert response.page.offset == 1
    assert response.page.limit == 2
    assert response.page.returned == 2
    assert response.page.total == 5
    assert response.page.next_offset == 3
    assert response.metadata.observation_count == 5


@pytest.mark.asyncio
async def test_gridstatus_load_forecast_failure_is_provenanced() -> None:
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    client = MagicMock()
    client.get_load.return_value = pd.DataFrame(
        [{"Interval Start": start, "Alberta Internal Load": 9000.0}]
    )
    client.get_load_forecast.side_effect = Exception("503 upstream")
    provider = GridStatusProvider(_settings())
    provider._client = client

    rows, metadata = await provider.get_load(
        start,
        start + timedelta(hours=1),
        include_forecast=True,
    )

    assert rows[0]["load_forecast_mw"] is None
    assert metadata["available_series"] == ["load"]
    assert metadata["missing_series"] == ["load_forecast"]
    assert metadata["completeness"] == "partial"


@pytest.mark.asyncio
async def test_partial_wind_solar_series_is_visible_in_generation_response() -> None:
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    client = MagicMock()
    client.get_wind_hourly.side_effect = Exception("503 upstream")
    client.get_solar_hourly.return_value = pd.DataFrame(
        [
            {
                "Interval Start": start,
                "Interval End": start + timedelta(hours=1),
                "Solar": 120.0,
            }
        ]
    )
    provider = GridStatusProvider(_settings())
    provider._client = client
    market = MarketService(provider, _settings())

    response = await market.get_generation(
        GenerationRequest(start=start, end=start + timedelta(hours=1))
    )

    assert response.metadata.available_series == ["Solar"]
    assert response.metadata.missing_series == ["Wind"]
    assert response.metadata.completeness == DataCompleteness.PARTIAL
    assert any("partial" in warning.lower() for warning in response.warnings)


@pytest.mark.asyncio
async def test_analytics_load_context_degradation_is_visible() -> None:
    provider = AsyncMock()
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    provider.get_pool_prices.return_value = (
        [
            PoolPriceInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                pool_price_cad_per_mwh=200.0,
            )
        ],
        {"provider": "gridstatus", "source_product": "Pool Price API"},
    )
    provider.get_load.side_effect = UpstreamUnavailableError("load unavailable")
    market = MarketService(provider, _settings())
    analytics = AnalyticsService(market, _settings())

    result = await analytics.find_price_events(
        FindPriceEventsRequest(
            start=start,
            end=start + timedelta(hours=1),
            threshold_cad_per_mwh=100.0,
        )
    )

    assert result.metadata.completeness == DataCompleteness.DEGRADED
    assert result.metadata.missing_series == ["load"]
    assert any("load context" in warning.lower() for warning in result.warnings)
    assert result.events


@pytest.mark.asyncio
async def test_mcp_domain_errors_are_safe_machine_readable_json() -> None:
    @map_errors
    async def failing_tool() -> None:
        raise InvalidDateRangeError("Invalid range")

    with pytest.raises(ValueError) as caught:
        await failing_tool()

    payload = json.loads(str(caught.value))
    assert payload == {"error": {"code": "InvalidDateRangeError", "message": "Invalid range"}}
    assert "AESO_API_KEY" not in str(caught.value)
