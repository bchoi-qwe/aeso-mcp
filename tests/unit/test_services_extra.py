# SPDX-License-Identifier: MIT
"""Additional service-layer coverage."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.models.analytics import ExplainMarketConditionsRequest
from aeso_mcp.models.assets import AssetsRequest
from aeso_mcp.models.generation import FuelMixComponent, GenerationRequest
from aeso_mcp.models.grid import InterchangePathFlow, OutagesRequest
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.services.assets import AssetsService
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.grid import GridService
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import MARKET_TZ


def _settings() -> Settings:
    return Settings(aeso_api_key="test-key")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_generation_snapshot_and_history() -> None:
    provider = AsyncMock()
    observed = datetime(2024, 1, 15, 12, tzinfo=MARKET_TZ)
    provider.get_fuel_mix.return_value = (
        observed,
        [
            FuelMixComponent(fuel_type="Wind", generation_mw=100.0),
            FuelMixComponent(fuel_type="Coal", generation_mw=900.0),
        ],
        {"provider": "gridstatus", "source_product": "CSD"},
    )
    provider.get_generation_history.return_value = (
        [],
        {"provider": "gridstatus", "source_product": "Wind"},
    )
    market = MarketService(provider, _settings(), AsyncTTLCache())
    snap = await market.get_generation(GenerationRequest())
    assert snap.snapshot is not None
    assert snap.snapshot.renewable_share == pytest.approx(0.1)

    hist = await market.get_generation(
        GenerationRequest(
            start=observed,
            end=observed + timedelta(days=1),
        )
    )
    assert hist.warnings


@pytest.mark.asyncio
async def test_grid_and_assets_services() -> None:
    provider = AsyncMock()
    observed = datetime(2024, 1, 15, 12, tzinfo=MARKET_TZ)
    provider.get_interchange.return_value = (
        observed,
        [InterchangePathFlow(path="BC", flow_mw=1.0)],
        1.0,
        {"provider": "gridstatus", "source_product": "CSD"},
    )
    provider.get_reserves.return_value = (
        observed,
        {"contingency_reserve_required_mw": 10.0},
        {"provider": "gridstatus", "source_product": "CSD"},
    )
    provider.get_outages.return_value = (
        [],
        {"provider": "gridstatus", "source_product": "Outages"},
    )
    provider.get_assets.return_value = (
        [],
        {"provider": "gridstatus", "source_product": "Assets"},
    )
    cache = AsyncTTLCache()
    grid = GridService(provider, _settings(), cache)
    assets = AssetsService(provider, _settings(), cache)
    inter = await grid.get_interchange()
    assert inter.net_interchange_mw == 1.0
    reserves = await grid.get_reserves()
    assert reserves.contingency_reserve_required_mw == 10.0
    outages = await grid.get_outages(
        OutagesRequest(start=observed, end=observed + timedelta(days=1))
    )
    assert outages.warnings
    asset_resp = await assets.get_assets(AssetsRequest(limit=10))
    assert asset_resp.assets == []


@pytest.mark.asyncio
async def test_explain_market_conditions() -> None:
    provider = AsyncMock()
    start = datetime(2024, 1, 10, tzinfo=MARKET_TZ)

    async def prices(s: datetime, e: datetime):
        from aeso_mcp.models.prices import PoolPriceInterval

        hours = max(int((e - s).total_seconds() // 3600), 1)
        level = 100.0 if s >= start else 50.0
        return (
            [
                PoolPriceInterval(
                    interval_start=s + timedelta(hours=i),
                    interval_end=s + timedelta(hours=i + 1),
                    pool_price_cad_per_mwh=level,
                )
                for i in range(hours)
            ],
            {"provider": "gridstatus", "source_product": "Pool Price API"},
        )

    provider.get_pool_prices.side_effect = prices
    provider.get_load.return_value = ([], {"provider": "gridstatus", "source_product": "Load"})
    market = MarketService(provider, _settings())
    analytics = AnalyticsService(market, _settings())
    result = await analytics.explain_market_conditions(
        ExplainMarketConditionsRequest(
            start=start,
            end=start + timedelta(hours=3),
        )
    )
    assert result.associated_changes
    assert any("causation" in w.lower() or "correl" in w.lower() for w in result.warnings)
