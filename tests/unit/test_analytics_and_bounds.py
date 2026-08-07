# SPDX-License-Identifier: MIT
"""Unit tests for query bounds and analytics helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.errors import InvalidDateRangeError, QueryTooLargeError
from aeso_mcp.models.analytics import FindPriceEventsRequest
from aeso_mcp.models.prices import (
    PoolPriceInterval,
    PoolPriceRequest,
)
from aeso_mcp.services.analytics import AnalyticsService, _percentile
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import MARKET_TZ


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "aeso_api_key": "test-key-not-real",
        "max_smp_observations": 20_000,
        "max_price_observations": 10_000,
        "max_pool_price_days": 366,
        "max_smp_days": 7,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pool_price_range_bound() -> None:
    provider = AsyncMock()
    service = MarketService(provider, _settings(max_pool_price_days=3))
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    end = start + timedelta(days=10)
    with pytest.raises(InvalidDateRangeError):
        await service.get_pool_prices(PoolPriceRequest(start=start, end=end))


@pytest.mark.asyncio
async def test_pool_price_observation_cap() -> None:
    provider = AsyncMock()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    intervals = [
        PoolPriceInterval(
            interval_start=start + timedelta(hours=i),
            interval_end=start + timedelta(hours=i + 1),
            pool_price_cad_per_mwh=50.0 + i,
        )
        for i in range(8)
    ]
    provider.get_pool_prices.return_value = (
        intervals,
        {"provider": "gridstatus", "source_product": "Pool Price API"},
    )
    service = MarketService(provider, _settings(max_price_observations=5))
    with pytest.raises(QueryTooLargeError):
        await service.get_pool_prices(PoolPriceRequest(start=start, end=start + timedelta(days=1)))


def test_percentile_helper() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(values, 0) == 10.0
    assert _percentile(values, 100) == 50.0
    assert _percentile(values, 50) == 30.0


@pytest.mark.asyncio
async def test_find_price_events_detects_run() -> None:
    provider = AsyncMock()
    start = datetime(2024, 6, 1, tzinfo=MARKET_TZ)
    prices = []
    for i in range(10):
        price = 200.0 if 3 <= i <= 6 else 40.0
        prices.append(
            PoolPriceInterval(
                interval_start=start + timedelta(hours=i),
                interval_end=start + timedelta(hours=i + 1),
                pool_price_cad_per_mwh=price,
            )
        )
    provider.get_pool_prices.return_value = (
        prices,
        {"provider": "gridstatus", "source_product": "Pool Price API"},
    )
    provider.get_load.return_value = (
        [
            {
                "interval_start": start + timedelta(hours=i),
                "interval_end": start + timedelta(hours=i + 1),
                "load_mw": 9000.0 + i,
                "load_forecast_mw": None,
            }
            for i in range(10)
        ],
        {"provider": "gridstatus", "source_product": "Load"},
    )
    market = MarketService(provider, _settings())
    analytics = AnalyticsService(market, _settings())
    result = await analytics.find_price_events(
        FindPriceEventsRequest(
            start=start,
            end=start + timedelta(hours=10),
            threshold_cad_per_mwh=100.0,
            min_duration_hours=2.0,
        )
    )
    assert len(result.events) == 1
    assert result.events[0].peak_price_cad_per_mwh == 200.0
    assert result.events[0].duration_hours == 4.0


@pytest.mark.asyncio
async def test_cache_single_flight() -> None:
    cache = AsyncTTLCache()
    calls = {"n": 0}

    async def factory() -> str:
        calls["n"] += 1
        return "value"

    import asyncio

    results = await asyncio.gather(
        cache.get_or_set("k", factory, ttl_s=60),
        cache.get_or_set("k", factory, ttl_s=60),
        cache.get_or_set("k", factory, ttl_s=60),
    )
    assert results == ["value", "value", "value"]
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_compare_periods() -> None:
    provider = AsyncMock()

    async def prices(start: datetime, end: datetime):
        hours = int((end - start).total_seconds() // 3600)
        base = 50.0 if start.day == 1 else 100.0
        return (
            [
                PoolPriceInterval(
                    interval_start=start + timedelta(hours=i),
                    interval_end=start + timedelta(hours=i + 1),
                    pool_price_cad_per_mwh=base,
                )
                for i in range(max(hours, 1))
            ],
            {"provider": "gridstatus", "source_product": "Pool Price API"},
        )

    provider.get_pool_prices.side_effect = prices
    provider.get_load.return_value = ([], {"provider": "gridstatus", "source_product": "Load"})
    market = MarketService(provider, _settings())
    analytics = AnalyticsService(market, _settings())
    from aeso_mcp.models.analytics import CompareMarketPeriodsRequest

    a0 = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    a1 = datetime(2024, 1, 2, tzinfo=MARKET_TZ)
    b0 = datetime(2024, 1, 3, tzinfo=MARKET_TZ)
    b1 = datetime(2024, 1, 4, tzinfo=MARKET_TZ)
    result = await analytics.compare_market_periods(
        CompareMarketPeriodsRequest(
            period_a_start=a0,
            period_a_end=a1,
            period_b_start=b0,
            period_b_end=b1,
        )
    )
    assert result.period_a.avg_pool_price_cad_per_mwh == 50.0
    assert result.period_b.avg_pool_price_cad_per_mwh == 100.0
    assert result.price_avg_delta_cad_per_mwh == 50.0
