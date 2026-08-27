# SPDX-License-Identifier: MIT
"""DuckDB/Parquet historical-store behavior."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from aeso_mcp.config import Settings
from aeso_mcp.models.common import DataCompleteness, FinalityStatus, ObservationType
from aeso_mcp.models.generation import LoadRequest
from aeso_mcp.models.history import (
    HistoricalDataset,
    HistoricalGenerationInterval,
    HistoricalStoreSyncRequest,
)
from aeso_mcp.models.prices import PoolPriceRequest
from aeso_mcp.providers.historical_generation import HistoricalGenerationProvider
from aeso_mcp.providers.public_reports import AesoPublicReportsProvider
from aeso_mcp.services.history import HistoryService
from aeso_mcp.services.market import MarketService
from aeso_mcp.storage.history import HistoricalStore


def test_generation_source_replace_is_idempotent_and_builds_partition(tmp_path) -> None:
    store = HistoricalStore(tmp_path / "history")
    if not store.dependencies_available():
        pytest.skip("analytics extra is not installed")
    start = datetime(2026, 7, 1, tzinfo=UTC)
    record = HistoricalGenerationInterval(
        interval_start=start,
        interval_end=start + timedelta(hours=1),
        interval_start_utc=start,
        interval_end_utc=start + timedelta(hours=1),
        resolution="hourly",
        asset_id="TEST1",
        asset_name="Test Asset",
        fuel_type="WIND",
        generation_mw=10,
        maximum_capability_mw=20,
        source_file_id="123",
        source_file_name="CSD Generation (Hourly) - 2026-07.zip",
        source_retrieved_at=start,
    )

    written, partitions = store.replace_generation_source(
        [record, record.model_copy(update={"generation_mw": 10.5})],
        source_file_id="123",
        source_file_name=record.source_file_name,
        source_hash="abc123",
        source_updated_at=None,
    )
    assert (written, partitions) == (1, 1)
    assert store.source_is_current(HistoricalDataset.HISTORICAL_GENERATION, "123", "abc123")

    store.replace_generation_source(
        [record.model_copy(update={"generation_mw": 11})],
        source_file_id="123",
        source_file_name=record.source_file_name,
        source_hash="def456",
        source_updated_at=None,
    )
    rows, total = store.query_generation(
        start=start,
        end=start + timedelta(hours=1),
        resolution="hourly",
        asset_ids=["TEST1"],
        fuel_types=[],
        offset=0,
        limit=10,
    )
    assert total == 1
    assert rows[0].generation_mw == 11
    assert len(list(store.parquet_root.rglob("data.parquet"))) == 1


def test_preliminary_market_intervals_remain_refreshable_until_final(tmp_path) -> None:
    store = HistoricalStore(tmp_path / "history")
    if not store.dependencies_available():
        pytest.skip("analytics extra is not installed")
    start = datetime(2026, 7, 1, tzinfo=UTC)
    base_row = {
        "interval_start": start,
        "interval_end": start + timedelta(hours=1),
        "series": "pool_price",
        "actual_value": 42.0,
        "forecast_value": None,
        "unit": "CAD/MWh",
        "source_product": "Pool Price Report",
        "source_retrieved_at": start + timedelta(hours=2),
        "observation_type": "actual",
        "finality": "preliminary",
        "completeness": "complete",
    }

    written, _ = store.upsert_market_series(
        HistoricalDataset.POOL_PRICE,
        [base_row, {**base_row, "actual_value": 42.5}],
    )
    assert written == 1
    assert store.missing_market_ranges("pool_price", start, start + timedelta(hours=1)) == [
        (start, start + timedelta(hours=1))
    ]

    store.upsert_market_series(
        HistoricalDataset.POOL_PRICE,
        [{**base_row, "finality": "final", "actual_value": 43.0}],
    )
    assert store.missing_market_ranges("pool_price", start, start + timedelta(hours=1)) == []


def test_market_series_queries_are_typed_paginated_and_dst_safe(tmp_path) -> None:
    store = HistoricalStore(tmp_path / "history")
    if not store.dependencies_available():
        pytest.skip("analytics extra is not installed")
    market_tz = ZoneInfo("America/Edmonton")
    start = datetime(2025, 11, 2, tzinfo=market_tz)
    end = datetime(2025, 11, 3, tzinfo=market_tz)
    start_utc = start.astimezone(UTC)
    rows = [
        {
            "interval_start": start_utc + timedelta(hours=index),
            "interval_end": start_utc + timedelta(hours=index + 1),
            "series": "pool_price",
            "actual_value": float(index),
            "forecast_value": float(index) + 0.5,
            "unit": "CAD/MWh",
            "source_product": "Pool Price Report",
            "source_retrieved_at": start_utc + timedelta(days=2),
            "finality": "final",
            "completeness": "complete",
        }
        for index in range(25)
    ]
    assert store.upsert_market_series(HistoricalDataset.POOL_PRICE, rows) == (25, 1)

    page, total = store.query_pool_price(
        start=start,
        end=end,
        offset=3,
        limit=5,
    )
    assert total == 25
    assert len(page) == 5
    assert page[0].actual_value == 3
    assert page[0].series == "pool_price"

    forecast, forecast_total = store.query_market_series(
        "pool_price_forecast",
        start=start,
        end=end,
        paginate=False,
        value_kind="forecast",
    )
    assert forecast_total == 25
    assert len(forecast) == 25
    assert forecast[-1].forecast_value == 24.5

    coverage = store.market_series_coverage("pool_price", start=start, end=end)
    assert coverage.expected_observations == 25
    assert coverage.observed_observations == 25
    assert coverage.missing_observations == 0
    assert coverage.complete is True
    assert coverage.finality == "final"
    assert coverage.source_products == ["Pool Price Report"]


@pytest.mark.asyncio
async def test_history_service_offloads_blocking_store_status(tmp_path) -> None:
    store = HistoricalStore(tmp_path / "history")
    store.dependencies_available = lambda: True  # type: ignore[method-assign]

    def blocking_status() -> list[object]:
        time.sleep(0.05)
        return []

    store.statuses = blocking_status  # type: ignore[method-assign]
    service = HistoryService(
        cast(HistoricalGenerationProvider, SimpleNamespace()),
        cast(AesoPublicReportsProvider, SimpleNamespace()),
        cast(MarketService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key"), history_store_path=Path(tmp_path / "history")),
        store=store,
    )
    ticked = False

    async def ticker() -> None:
        nonlocal ticked
        await asyncio.sleep(0.01)
        ticked = True

    await asyncio.gather(service.get_historical_store_status(), ticker())
    assert ticked is True


@pytest.mark.asyncio
async def test_history_service_reads_complete_price_and_load_series_from_store(tmp_path) -> None:
    store = HistoricalStore(tmp_path / "history")
    if not store.dependencies_available():
        pytest.skip("analytics extra is not installed")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    retrieved_at = end + timedelta(days=1)
    common = {
        "source_retrieved_at": retrieved_at,
        "observation_type": ObservationType.ACTUAL,
        "finality": FinalityStatus.FINAL,
        "completeness": DataCompleteness.COMPLETE,
    }
    price_rows = [
        {
            **common,
            "interval_start": start + timedelta(hours=index),
            "interval_end": start + timedelta(hours=index + 1),
            "series": "pool_price",
            "actual_value": 40.0 + index,
            "forecast_value": 39.0 + index,
            "unit": "CAD/MWh",
            "source_product": "Pool Price Report",
        }
        for index in range(24)
    ]
    load_rows = [
        {
            **common,
            "interval_start": start + timedelta(hours=index),
            "interval_end": start + timedelta(hours=index + 1),
            "series": "ail",
            "actual_value": 10_000.0 + index,
            "forecast_value": 9_990.0 + index,
            "unit": "MW",
            "source_product": "Actual Forecast Report",
        }
        for index in range(24)
    ]
    store.upsert_market_series(HistoricalDataset.POOL_PRICE, price_rows)
    store.upsert_market_series(HistoricalDataset.LOAD, load_rows)
    market = SimpleNamespace(get_pool_prices=AsyncMock(), get_load=AsyncMock())
    service = HistoryService(
        cast(HistoricalGenerationProvider, SimpleNamespace()),
        cast(AesoPublicReportsProvider, SimpleNamespace()),
        cast(MarketService, market),
        Settings(aeso_api_key=SecretStr("test-key"), history_store_path=tmp_path / "history"),
        store=store,
    )

    prices = await service.get_historical_pool_prices(
        PoolPriceRequest(start=start, end=end, include_forecast=True, offset=2, limit=3)
    )
    loads = await service.get_historical_load(
        LoadRequest(start=start, end=end, include_forecast=True, offset=4, limit=2)
    )

    assert prices.page.total == 24
    assert [item.pool_price_cad_per_mwh for item in prices.intervals] == [42.0, 43.0, 44.0]
    assert prices.intervals[0].forecast_pool_price_cad_per_mwh == 41.0
    assert prices.metadata.extra["storage"] == "duckdb"
    assert prices.metadata.extra["source_products"] == ["Pool Price Report"]
    assert loads.page.total == 24
    assert [item.load_mw for item in loads.intervals] == [10_004.0, 10_005.0]
    assert loads.intervals[0].load_forecast_mw == 9_994.0
    assert loads.metadata.extra["storage"] == "duckdb"
    market.get_pool_prices.assert_not_awaited()
    market.get_load.assert_not_awaited()


@pytest.mark.asyncio
async def test_year_long_load_sync_is_chunked_to_upstream_limits(tmp_path) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 1, tzinfo=UTC)
    missing_calls = 0

    def missing_market_ranges(
        series: str, range_start: datetime, range_end: datetime
    ) -> list[tuple[datetime, datetime]]:
        nonlocal missing_calls
        assert series == "ail"
        missing_calls += 1
        return [(range_start, range_end)] if missing_calls == 1 else []

    store = SimpleNamespace(
        root=tmp_path / "history",
        schema_version=2,
        initialize=lambda: None,
        missing_market_ranges=missing_market_ranges,
        upsert_market_series=lambda dataset, rows: (len(rows), 1),
    )

    async def get_load(request: LoadRequest, *, paginate: bool) -> SimpleNamespace:
        assert paginate is False
        return SimpleNamespace(
            intervals=[
                SimpleNamespace(
                    interval_start=request.start,
                    interval_end=request.end,
                    load_mw=10_000.0,
                    load_forecast_mw=9_950.0,
                )
            ],
            metadata=SimpleNamespace(
                source_product="Actual Forecast Report",
                retrieved_at=end,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.FINAL,
                completeness=DataCompleteness.COMPLETE,
            ),
            warnings=[],
        )

    market = SimpleNamespace(get_load=AsyncMock(side_effect=get_load))
    service = HistoryService(
        cast(HistoricalGenerationProvider, SimpleNamespace()),
        cast(AesoPublicReportsProvider, SimpleNamespace()),
        cast(MarketService, market),
        Settings(
            aeso_api_key=SecretStr("test-key"),
            history_store_path=tmp_path / "history",
            max_load_days=90,
        ),
        store=cast(HistoricalStore, store),
    )

    response = await service.sync_historical_store(
        HistoricalStoreSyncRequest(
            start=start,
            end=end,
            datasets=[HistoricalDataset.LOAD],
        )
    )

    assert market.get_load.await_count == 5
    for call in market.get_load.await_args_list:
        request = call.args[0]
        assert request.end - request.start <= timedelta(days=90)
    result = response.results[0]
    assert result.records_received == 5
    assert result.records_written == 5
    assert result.partitions_rebuilt == 5
    assert result.missing_interval_count == 0
