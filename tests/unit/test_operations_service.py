# SPDX-License-Identifier: MIT
"""Bounds, pagination, aggregation, and supply-margin tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.errors import InvalidDateRangeError, QueryTooLargeError
from aeso_mcp.models.common import DataCompleteness, DatasetMetadata, ProviderName
from aeso_mcp.models.generation import LoadInterval, LoadResponse
from aeso_mcp.models.grid import MarketSnapshotResponse
from aeso_mcp.models.operations import (
    DailyPageRequest,
    EnergyMeritOrderBlock,
    GenerationCapacityInterval,
    MarketHistorySummaryRequest,
    MeteredVolumeRequest,
    PageInfo,
)
from aeso_mcp.models.prices import PoolPriceInterval, PoolPriceResponse
from aeso_mcp.providers.capabilities import OperationalReportsProvider
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.market import MarketService
from aeso_mcp.services.operations import OperationsService
from aeso_mcp.timeutil import MARKET_TZ, add_elapsed, market_now, utc_now


def _metadata(dataset: str) -> DatasetMetadata:
    return DatasetMetadata(
        dataset=dataset,
        retrieved_at=utc_now(),
        provider=ProviderName.DERIVED,
    )


def _service() -> tuple[OperationsService, AsyncMock, AsyncMock]:
    provider = AsyncMock()
    market = AsyncMock()
    settings = Settings(aeso_api_key="test-key")  # type: ignore[arg-type]
    service = OperationsService(
        cast(OperationalReportsProvider, provider),
        cast(MarketService, market),
        settings,
        AsyncTTLCache(),
    )
    return service, provider, market


def test_metered_volume_identifiers_are_individual_and_normalized() -> None:
    request = MeteredVolumeRequest(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1),
        asset_ids=["  GEN1  "],
    )
    assert request.asset_ids == ["GEN1"]

    with pytest.raises(ValueError, match="individual values"):
        MeteredVolumeRequest(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 1),
            asset_ids=["GEN1,GEN2"],
        )


@pytest.mark.asyncio
async def test_energy_merit_order_paginates_stably_and_reuses_cached_report() -> None:
    service, provider, _ = _service()
    instant = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    provider.get_energy_merit_order.return_value = (
        [
            EnergyMeritOrderBlock(
                interval_start=instant,
                asset_id=f"GEN{index}",
                block_number=index,
                block_price_cad_per_mwh=float(index),
            )
            for index in range(3)
        ],
        {
            "provider": "aeso_apim",
            "source_product": "Energy Merit Order Report",
            "api_version": "v1",
        },
    )

    first = await service.get_energy_merit_order(
        DailyPageRequest(report_date=date(2024, 1, 15), offset=0, limit=2)
    )
    second = await service.get_energy_merit_order(
        DailyPageRequest(report_date=date(2024, 1, 15), offset=2, limit=2)
    )

    assert [row.asset_id for row in first.blocks] == ["GEN0", "GEN1"]
    assert first.page.total == 3
    assert first.page.next_offset == 2
    assert [row.asset_id for row in second.blocks] == ["GEN2"]
    assert second.page.next_offset is None
    assert second.metadata.cache_hit is True
    provider.get_energy_merit_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_merit_order_enforces_publication_delay() -> None:
    service, _, _ = _service()
    too_recent = market_now().date() - timedelta(days=59)

    with pytest.raises(InvalidDateRangeError, match="latest available date"):
        await service.get_energy_merit_order(DailyPageRequest(report_date=too_recent))


@pytest.mark.asyncio
async def test_market_history_summary_aggregates_in_market_day() -> None:
    service, _, market = _service()
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    market.get_pool_prices.return_value = PoolPriceResponse(
        intervals=[
            PoolPriceInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                pool_price_cad_per_mwh=50,
            ),
            PoolPriceInterval(
                interval_start=start + timedelta(hours=1),
                interval_end=start + timedelta(hours=2),
                pool_price_cad_per_mwh=150,
            ),
        ],
        page=PageInfo(offset=0, limit=500, returned=2, total=2),
        metadata=_metadata("prices").model_copy(
            update={
                "completeness": DataCompleteness.PARTIAL,
                "available_series": ["pool_price"],
                "missing_series": ["pool_price_interval"],
            }
        ),
    )
    market.get_load.return_value = LoadResponse(
        intervals=[
            LoadInterval(interval_start=start, load_mw=9_000),
            LoadInterval(interval_start=start + timedelta(hours=1), load_mw=10_000),
        ],
        page=PageInfo(offset=0, limit=500, returned=2, total=2),
        metadata=_metadata("load"),
    )

    result = await service.summarize_market_history(
        MarketHistorySummaryRequest(
            start=start,
            end=start + timedelta(days=1),
            bucket="day",
            include_load=True,
        )
    )

    assert len(result.buckets) == 1
    assert result.buckets[0].average_pool_price_cad_per_mwh == 100
    assert result.buckets[0].median_pool_price_cad_per_mwh == 100
    assert result.buckets[0].hours_at_or_above_100_cad_per_mwh == 1
    assert result.buckets[0].average_load_mw == 9_500
    assert result.metadata.observation_count == 1
    assert result.metadata.completeness == DataCompleteness.DEGRADED
    assert result.metadata.missing_series == ["pool_price_interval"]


@pytest.mark.asyncio
async def test_hourly_summary_rejects_context_heavy_range() -> None:
    service, _, _ = _service()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)

    with pytest.raises(QueryTooLargeError, match="maximum is 400"):
        await service.summarize_market_history(
            MarketHistorySummaryRequest(
                start=start,
                end=start + timedelta(days=30),
                bucket="hour",
            )
        )


@pytest.mark.asyncio
async def test_hourly_summary_keeps_both_fall_back_hours() -> None:
    service, _, market = _service()
    first = datetime(2024, 11, 3, 1, tzinfo=MARKET_TZ, fold=0)
    second = datetime(2024, 11, 3, 1, tzinfo=MARKET_TZ, fold=1)
    market.get_pool_prices.return_value = PoolPriceResponse(
        intervals=[
            PoolPriceInterval(
                interval_start=first,
                interval_end=add_elapsed(first, timedelta(hours=1)),
                pool_price_cad_per_mwh=25,
            ),
            PoolPriceInterval(
                interval_start=second,
                interval_end=add_elapsed(second, timedelta(hours=1)),
                pool_price_cad_per_mwh=75,
            ),
        ],
        page=PageInfo(offset=0, limit=2, returned=2, total=2),
        metadata=_metadata("prices"),
    )

    result = await service.summarize_market_history(
        MarketHistorySummaryRequest(
            start=datetime(2024, 11, 3, 0, tzinfo=MARKET_TZ),
            end=datetime(2024, 11, 3, 3, tzinfo=MARKET_TZ),
            bucket="hour",
            include_load=False,
        )
    )

    assert len(result.buckets) == 2
    assert [bucket.interval_start.fold for bucket in result.buckets] == [0, 1]
    assert [bucket.average_pool_price_cad_per_mwh for bucket in result.buckets] == [25, 75]


@pytest.mark.asyncio
async def test_supply_tightness_uses_transparent_reserve_adjusted_margin() -> None:
    service, provider, market = _service()
    observed = market_now().replace(minute=0, second=0, microsecond=0)
    market.get_market_snapshot.return_value = MarketSnapshotResponse(
        observed_at=observed,
        alberta_internal_load_mw=10_000,
        net_interchange_mw=100,
        contingency_reserve_required_mw=400,
        metadata=_metadata("snapshot"),
    )
    provider.get_generation_capacity.return_value = (
        [
            GenerationCapacityInterval(
                interval_start=observed,
                fuel_type="Gas",
                maximum_capability_mw=12_000,
                available_capability_mw=11_000,
                operating_outage_mw=800,
                mothball_outage_mw=200,
            )
        ],
        {
            "provider": "aeso_apim",
            "source_product": "AIES Gen Capacity API",
            "api_version": "v1",
        },
    )

    result = await service.assess_supply_tightness()

    assert result.gross_supply_margin_mw == 1_100
    assert result.reserve_adjusted_margin_mw == 700
    assert result.reserve_adjusted_margin_pct_of_load == pytest.approx(0.07)
    assert result.tightness_signal == "comfortable"
    assert "not an AESO declaration" in result.methodology


@pytest.mark.asyncio
async def test_supply_tightness_does_not_treat_missing_interchange_as_zero() -> None:
    service, provider, market = _service()
    observed = market_now().replace(minute=0, second=0, microsecond=0)
    market.get_market_snapshot.return_value = MarketSnapshotResponse(
        observed_at=observed,
        alberta_internal_load_mw=10_000,
        net_interchange_mw=None,
        contingency_reserve_required_mw=400,
        metadata=_metadata("snapshot"),
    )
    provider.get_generation_capacity.return_value = (
        [
            GenerationCapacityInterval(
                interval_start=observed,
                fuel_type="Gas",
                available_capability_mw=11_000,
            )
        ],
        {"provider": "aeso_apim", "source_product": "AIES Gen Capacity API"},
    )

    result = await service.assess_supply_tightness()

    assert result.gross_supply_margin_mw is None
    assert result.reserve_adjusted_margin_mw is None
    assert result.tightness_signal == "unknown"
    assert "net_interchange" in result.metadata.missing_series
