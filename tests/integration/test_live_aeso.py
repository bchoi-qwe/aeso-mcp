# SPDX-License-Identifier: MIT
"""Opt-in live AESO integration tests.

These hit real AESO APIs. Keep windows short and coverage intentional so we
smoke the production GridStatus path without hammering upstream.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings, clear_settings_cache
from aeso_mcp.models.assets import AssetsRequest
from aeso_mcp.models.generation import LoadRequest
from aeso_mcp.models.grid import OutagesRequest
from aeso_mcp.models.prices import SystemMarginalPriceRequest
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
    assert intervals[0].interval_start.tzinfo is not None


@pytest.mark.asyncio
async def test_live_fuel_mix(live_settings: Settings) -> None:
    provider = GridStatusProvider(live_settings)
    observed_at, components, _ = await provider.get_fuel_mix()
    assert observed_at.tzinfo is not None
    assert len(components) >= 1


@pytest.mark.asyncio
async def test_live_market_snapshot_via_services(live_settings: Settings) -> None:
    container = build_container(live_settings)
    snap = await container.market.get_market_snapshot()
    assert snap.observed_at.tzinfo is not None
    assert snap.alberta_internal_load_mw is not None
    assert snap.alberta_internal_load_mw > 0
    assert snap.total_generation_mw is not None
    assert snap.generation_by_fuel
    assert snap.pool_price_cad_per_mwh is not None
    assert snap.metadata.market_timezone == "America/Edmonton"


@pytest.mark.asyncio
async def test_live_load_and_smp_short_windows(live_settings: Settings) -> None:
    container = build_container(live_settings)
    end = market_now()
    start = end - timedelta(hours=6)
    load = await container.market.get_load(LoadRequest(start=start, end=end))
    assert load.intervals
    assert all(i.load_mw >= 0 for i in load.intervals)

    smp_start = end - timedelta(hours=2)
    smp = await container.market.get_system_marginal_prices(
        SystemMarginalPriceRequest(start=smp_start, end=end)
    )
    assert smp.intervals
    assert smp.intervals[0].system_marginal_price_cad_per_mwh >= 0


@pytest.mark.asyncio
async def test_live_interchange_reserves_assets(live_settings: Settings) -> None:
    container = build_container(live_settings)
    interchange = await container.grid.get_interchange()
    assert interchange.observed_at.tzinfo is not None
    assert interchange.paths

    reserves = await container.grid.get_reserves()
    assert reserves.observed_at.tzinfo is not None
    assert reserves.contingency_reserve_required_mw is not None

    assets = await container.assets.get_assets(AssetsRequest(limit=25))
    assert assets.assets
    assert assets.assets[0].asset_id


@pytest.mark.asyncio
async def test_live_outages_recent_day(live_settings: Settings) -> None:
    container = build_container(live_settings)
    end = market_now()
    start = end - timedelta(hours=24)
    result = await container.grid.get_outages(OutagesRequest(start=start, end=end))
    # Upstream may legitimately return zero outages; require a well-formed response.
    assert result.metadata.observation_count == len(result.outages)
    if not result.outages:
        assert result.warnings


@pytest.mark.asyncio
async def test_live_transmission_public_reports(live_settings: Settings) -> None:
    from aeso_mcp.models.market_power import MarketPowerMitigationRequest
    from aeso_mcp.models.transmission import (
        ApprovedTransmissionOutagesRequest,
        LongRangeTransmissionOutagesRequest,
    )

    container = build_container(live_settings)
    try:
        approved = await container.transmission.get_approved_transmission_outages(
            ApprovedTransmissionOutagesRequest()
        )
        assert approved.approval_status == "approved"
        assert approved.metadata.observation_count == len(approved.outages)

        long_range = await container.transmission.get_long_range_transmission_outages(
            LongRangeTransmissionOutagesRequest()
        )
        assert long_range.approval_status == "tentative"
        assert long_range.outages
        assert all(o.approval_status == "tentative" for o in long_range.outages)

        mcsinr = await container.market_power.get_monthly_cumulative_net_revenue(
            MarketPowerMitigationRequest()
        )
        assert mcsinr.intervals
        assert mcsinr.one_sixth_annualized_unavoidable_costs_cad is not None

        soc = await container.market_power.get_secondary_offer_price_limit(
            MarketPowerMitigationRequest()
        )
        assert soc.intervals
        assert soc.limit_in_effect is not None
    finally:
        await container.public_reports_http.aclose()
