# SPDX-License-Identifier: MIT
"""High-price and negative-price event detection without fabricated intervals."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.models.analytics import FindPriceEventsRequest
from aeso_mcp.models.prices import PoolPriceInterval
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import MARKET_TZ


@pytest.mark.asyncio
async def test_negative_price_events_and_percentile_direction() -> None:
    start = datetime(2026, 7, 1, tzinfo=MARKET_TZ)
    provider = AsyncMock()
    values = [-30.0, -45.0, 10.0, -25.0, 70.0]
    provider.get_pool_prices.return_value = (
        [
            PoolPriceInterval(
                interval_start=start + timedelta(hours=i),
                interval_end=start + timedelta(hours=i + 1),
                pool_price_cad_per_mwh=value,
            )
            for i, value in enumerate(values)
        ],
        {"provider": "gridstatus", "source_product": "Pool Price"},
    )
    provider.get_load.return_value = (
        [],
        {"provider": "gridstatus", "source_product": "Load"},
    )
    settings = Settings(aeso_api_key="test-key")
    analytics = AnalyticsService(MarketService(provider, settings), settings)
    end = start + timedelta(hours=len(values))

    low = await analytics.find_price_events(
        FindPriceEventsRequest(
            start=start,
            end=end,
            direction="below",
            threshold_cad_per_mwh=-20,
        )
    )
    assert low.direction == "below"
    assert low.threshold_cad_per_mwh == -20
    assert len(low.events) == 2
    assert low.events[0].duration_hours == pytest.approx(2.0)
    assert low.events[0].minimum_price_cad_per_mwh == pytest.approx(-45.0)
    assert low.events[0].peak_price_cad_per_mwh == pytest.approx(-30.0)
    assert low.events[1].duration_hours == pytest.approx(1.0)

    percentile = await analytics.find_price_events(
        FindPriceEventsRequest(start=start, end=end, direction="below")
    )
    assert percentile.threshold_cad_per_mwh < 0
    assert len(percentile.events) == 1
    assert percentile.events[0].minimum_price_cad_per_mwh == -45.0

    high = await analytics.find_price_events(
        FindPriceEventsRequest(start=start, end=end, direction="above", threshold_cad_per_mwh=60)
    )
    assert high.direction == "above"
    assert len(high.events) == 1
    assert high.events[0].peak_price_cad_per_mwh == pytest.approx(70.0)
