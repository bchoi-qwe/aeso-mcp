# SPDX-License-Identifier: MIT
"""Opt-in live AESO integration tests."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from aeso_mcp.config import Settings, clear_settings_cache
from aeso_mcp.providers.gridstatus import GridStatusProvider
from aeso_mcp.timeutil import market_now

pytestmark = pytest.mark.integration


@pytest.fixture
def live_settings() -> Settings:
    key = os.environ.get("AESO_API_KEY")
    if not key:
        pytest.skip("AESO_API_KEY not set; skipping live integration tests")
    clear_settings_cache()
    return Settings(aeso_api_key=key)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_live_pool_price(live_settings: Settings) -> None:
    provider = GridStatusProvider(live_settings)
    end = market_now()
    start = end - timedelta(hours=6)
    intervals, meta = await provider.get_pool_prices(start, end)
    assert meta["provider"] == "gridstatus"
    assert len(intervals) >= 1
    assert intervals[0].pool_price_cad_per_mwh >= 0


@pytest.mark.asyncio
async def test_live_fuel_mix(live_settings: Settings) -> None:
    provider = GridStatusProvider(live_settings)
    observed_at, components, _ = await provider.get_fuel_mix()
    assert observed_at.tzinfo is not None
    assert len(components) >= 1
