# SPDX-License-Identifier: MIT
"""Unit tests for HistoryService covering archive retrieval, store sync, and fallback."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from aeso_mcp.config import Settings
from aeso_mcp.errors import InvalidDateRangeError
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    FinalityStatus,
    ObservationType,
    ProviderName,
)
from aeso_mcp.models.generation import LoadInterval, LoadRequest, LoadResponse
from aeso_mcp.models.history import (
    ForecastRequest,
    HistoricalDataset,
    HistoricalGenerationInterval,
    HistoricalGenerationRequest,
    HistoricalMarketSeriesInterval,
    HistoricalStoreSyncRequest,
    UnitCommitmentSettlementInterval,
    UnitCommitmentSettlementRequest,
)
from aeso_mcp.models.operations import PageInfo
from aeso_mcp.models.prices import PoolPriceInterval, PoolPriceRequest, PoolPriceResponse
from aeso_mcp.providers.historical_generation import HistoricalGenerationSourceFile
from aeso_mcp.services.history import HistoryService


def _settings() -> Settings:
    return Settings(
        aeso_api_key=SecretStr("test-key"),
        max_pool_price_days=366,
        max_load_days=90,
    )


def _page(total: int, offset: int = 0, limit: int = 500) -> PageInfo:
    returned = min(max(total - offset, 0), limit)
    next_offset = offset + returned if offset + returned < total else None
    return PageInfo(
        offset=offset, limit=limit, returned=returned, total=total, next_offset=next_offset
    )


def _meta(dataset: str, count: int = 1) -> DatasetMetadata:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return DatasetMetadata(
        dataset=dataset,
        retrieved_at=now,
        observation_count=count,
        observation_granularity="1h",
        provider=ProviderName.AESO_APIM,
        observation_type=ObservationType.ACTUAL,
        completeness=DataCompleteness.COMPLETE,
        finality=FinalityStatus.FINAL,
    )


def _gen_interval(start: datetime, asset_id: str = "GEN1") -> HistoricalGenerationInterval:
    return HistoricalGenerationInterval(
        interval_start=start,
        interval_end=start + timedelta(hours=1),
        interval_start_utc=start,
        interval_end_utc=start + timedelta(hours=1),
        resolution="hourly",
        asset_id=asset_id,
        fuel_type="WIND",
        generation_mw=50.0,
        source_file_id="src1",
        source_file_name="gen_2026_01.zip",
        source_retrieved_at=start,
    )


@pytest.mark.asyncio
async def test_get_historical_generation_no_sources_returns_empty() -> None:
    generation = AsyncMock()
    generation.list_source_files.return_value = []
    service = HistoryService(
        generation,
        AsyncMock(),
        AsyncMock(),
        _settings(),
        store=MagicMock(),
    )
    request = HistoricalGenerationRequest(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
        interval="hourly",
    )
    response = await service.get_historical_generation(request)
    assert response.intervals == []
    assert response.metadata.completeness == DataCompleteness.EMPTY
    assert "No official CSD archive file intersects" in response.warnings[0]


@pytest.mark.asyncio
async def test_get_historical_generation_from_store_when_current() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    source = HistoricalGenerationSourceFile(
        file_id="f1",
        name="test.zip",
        size_bytes=1000,
        content_updated_at=start,
        source_hash="h1",
        coverage_start=start.date(),
        coverage_end=end.date(),
    )
    generation = AsyncMock()
    generation.list_source_files.return_value = [source]

    store = MagicMock()
    store.dependencies_available.return_value = True
    store.source_is_current.return_value = True
    gen_row = _gen_interval(start)
    store.query_generation.return_value = ([gen_row], 1)

    service = HistoryService(
        generation,
        AsyncMock(),
        AsyncMock(),
        _settings(),
        store=store,
    )
    request = HistoricalGenerationRequest(start=start, end=end, interval="hourly")
    response = await service.get_historical_generation(request)

    assert len(response.intervals) == 1
    assert response.intervals[0].asset_id == "GEN1"
    assert response.metadata.extra.get("storage") == "duckdb"
    generation.fetch_source_file.assert_not_called()


@pytest.mark.asyncio
async def test_get_historical_generation_direct_archive_fallback() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    source = HistoricalGenerationSourceFile(
        file_id="f1",
        name="test.zip",
        size_bytes=1000,
        content_updated_at=start,
        source_hash="h1",
        coverage_start=start.date(),
        coverage_end=end.date(),
    )
    generation = AsyncMock()
    generation.list_source_files.return_value = [source]
    gen_row = _gen_interval(start)
    generation.fetch_source_file.return_value = ([gen_row], "digest1")

    # Store enabled but source not current
    store = MagicMock()
    store.dependencies_available.return_value = True
    store.source_is_current.return_value = False

    service = HistoryService(
        generation,
        AsyncMock(),
        AsyncMock(),
        _settings(),
        store=store,
    )
    request = HistoricalGenerationRequest(start=start, end=end, interval="hourly")
    response = await service.get_historical_generation(request, paginate=False)

    assert len(response.intervals) == 1
    assert response.metadata.extra.get("storage") == "direct"
    assert any("sync_historical_store" in w for w in response.warnings)

    # Store disabled
    store.dependencies_available.return_value = False
    response_no_store = await service.get_historical_generation(request)
    assert any("analytics extra" in w for w in response_no_store.warnings)


@pytest.mark.asyncio
async def test_get_historical_pool_prices_store_and_fallback() -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 1, 2, tzinfo=UTC)

    market_row = HistoricalMarketSeriesInterval(
        interval_start=start,
        interval_end=start + timedelta(hours=1),
        series="pool_price",
        actual_value=35.5,
        forecast_value=40.0,
        unit="CAD/MWh",
        source_product="Pool Price Report",
        source_retrieved_at=start,
        observation_type=ObservationType.ACTUAL,
        finality=FinalityStatus.FINAL,
        completeness=DataCompleteness.COMPLETE,
    )
    coverage = SimpleNamespace(
        complete=True,
        expected_observations=24,
        missing_observations=0,
        source_products=["Pool Price Report"],
        finality=FinalityStatus.FINAL,
    )
    store = MagicMock()
    store.dependencies_available.return_value = True
    store.schema_version = 3
    store.market_series_coverage.return_value = coverage
    store.query_market_series_complete.return_value = (
        [
            market_row.model_copy(
                update={
                    "interval_start": start + timedelta(hours=hour),
                    "interval_end": start + timedelta(hours=hour + 1),
                }
            )
            for hour in range(24)
        ],
        24,
    )

    market = AsyncMock()
    service = HistoryService(
        AsyncMock(),
        AsyncMock(),
        market,
        _settings(),
        store=store,
    )

    req_with_fc = PoolPriceRequest(start=start, end=end, include_forecast=True)
    resp = await service.get_historical_pool_prices(req_with_fc)
    assert len(resp.intervals) == 24
    assert resp.intervals[0].forecast_pool_price_cad_per_mwh == 40.0
    assert resp.metadata.extra.get("storage") == "duckdb"

    req_no_fc = PoolPriceRequest(start=start, end=end, include_forecast=False)
    resp_no_fc = await service.get_historical_pool_prices(req_no_fc)
    assert resp_no_fc.intervals[0].forecast_pool_price_cad_per_mwh is None

    # Store incomplete fallback
    coverage.complete = False
    market.get_pool_prices.return_value = PoolPriceResponse(
        intervals=[
            PoolPriceInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                pool_price_cad_per_mwh=35.5,
            )
        ],
        page=_page(1),
        metadata=_meta("Pool Price", 1),
        warnings=[],
    )
    fallback_resp = await service.get_historical_pool_prices(req_with_fc)
    assert fallback_resp.metadata.extra.get("storage") == "direct"
    assert any("retrieved directly" in w for w in fallback_resp.warnings)


@pytest.mark.asyncio
async def test_get_historical_load_store_and_fallback() -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 1, 2, tzinfo=UTC)

    market_row = HistoricalMarketSeriesInterval(
        interval_start=start,
        interval_end=start + timedelta(hours=1),
        series="ail",
        actual_value=9500.0,
        forecast_value=9600.0,
        unit="MW",
        source_product="Actual Forecast Report",
        source_retrieved_at=start,
        observation_type=ObservationType.ACTUAL,
        finality=FinalityStatus.FINAL,
        completeness=DataCompleteness.COMPLETE,
    )
    coverage = SimpleNamespace(
        complete=True,
        expected_observations=24,
        missing_observations=0,
        source_products=["Actual Forecast Report"],
        finality=FinalityStatus.FINAL,
    )
    store = MagicMock()
    store.dependencies_available.return_value = True
    store.schema_version = 3
    store.market_series_coverage.return_value = coverage
    store.query_market_series_complete.return_value = (
        [
            market_row.model_copy(
                update={
                    "interval_start": start + timedelta(hours=hour),
                    "interval_end": start + timedelta(hours=hour + 1),
                }
            )
            for hour in range(24)
        ],
        24,
    )

    market = AsyncMock()
    service = HistoryService(
        AsyncMock(),
        AsyncMock(),
        market,
        _settings(),
        store=store,
    )

    req = LoadRequest(start=start, end=end, include_forecast=True)
    resp = await service.get_historical_load(req)
    assert len(resp.intervals) == 24
    assert resp.intervals[0].load_mw == 9500.0
    assert resp.intervals[0].load_forecast_mw == 9600.0
    assert resp.metadata.extra.get("storage") == "duckdb"

    # Fallback to market
    coverage.complete = False
    market.get_load.return_value = LoadResponse(
        intervals=[
            LoadInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                load_mw=9500.0,
            )
        ],
        page=_page(1),
        metadata=_meta("Alberta Internal Load", 1),
        warnings=[],
    )
    fallback_resp = await service.get_historical_load(req)
    assert fallback_resp.metadata.extra.get("storage") == "direct"
    assert any("retrieved directly" in w for w in fallback_resp.warnings)


@pytest.mark.asyncio
async def test_get_forecast_as_of_and_no_as_of() -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 1, 2, tzinfo=UTC)
    as_of = datetime(2020, 1, 1, 12, tzinfo=UTC)

    store = MagicMock()
    store.dependencies_available.return_value = True
    forecast_row = SimpleNamespace(
        interval_start=start,
        interval_end=start + timedelta(hours=1),
        actual_value=42.0,
        forecast_value=40.0,
        unit="CAD/MWh",
        forecast_issue_time=start - timedelta(hours=2),
        publication_time=start - timedelta(hours=1),
        retrieved_at=start - timedelta(hours=1),
        source_version="v1",
        source_hash="h1",
        vintage_id="v1",
    )
    store.query_forecast.return_value = ([forecast_row], 1)

    market = AsyncMock()
    market.get_pool_prices.return_value = PoolPriceResponse(
        intervals=[
            PoolPriceInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                pool_price_cad_per_mwh=42.0,
                forecast_pool_price_cad_per_mwh=40.0,
            )
        ],
        page=_page(1),
        metadata=_meta("Pool Price", 1),
        warnings=[],
    )
    market.get_load.return_value = LoadResponse(
        intervals=[
            LoadInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                load_mw=9000.0,
                load_forecast_mw=8900.0,
            )
        ],
        page=_page(1),
        metadata=_meta("AIL", 1),
        warnings=[],
    )

    service = HistoryService(
        AsyncMock(),
        AsyncMock(),
        market,
        _settings(),
        store=store,
    )

    # 1. Point-in-time with as_of
    req_as_of = ForecastRequest(start=start, end=end, series="pool_price", as_of=as_of)
    resp_as_of = await service.get_forecast(req_as_of)
    assert len(resp_as_of.intervals) == 1
    assert resp_as_of.intervals[0].forecast_value == 40.0
    assert resp_as_of.metadata.extra.get("storage") == "duckdb"

    # 2. Point-in-time when store disabled
    store.dependencies_available.return_value = False
    resp_no_store = await service.get_forecast(req_as_of)
    assert resp_no_store.intervals == []
    assert any("require the optional historical-store" in w for w in resp_no_store.warnings)

    # 3. No as_of pool price
    req_no_as_of = ForecastRequest(start=start, end=end, series="pool_price", as_of=None)
    resp_pool = await service.get_forecast(req_no_as_of)
    assert len(resp_pool.intervals) == 1
    assert resp_pool.intervals[0].forecast_value == 40.0
    assert resp_pool.intervals[0].series == "pool_price"

    # 4. No as_of load
    req_load = ForecastRequest(start=start, end=end, series="ail", as_of=None)
    resp_load = await service.get_forecast(req_load)
    assert len(resp_load.intervals) == 1
    assert resp_load.intervals[0].forecast_value == 8900.0
    assert resp_load.intervals[0].series == "ail"


@pytest.mark.asyncio
async def test_get_uc_settlement_summary_delegates_and_validates_range() -> None:
    public_reports = AsyncMock()
    start_date = date(2026, 8, 1)
    end_date = date(2026, 8, 5)
    public_reports.get_unit_commitment_settlement_summary.return_value = (
        [
            UnitCommitmentSettlementInterval(
                interval_start=datetime(2026, 8, 1, 1, tzinfo=UTC),
                interval_end=datetime(2026, 8, 1, 2, tzinfo=UTC),
                hour_ending_label="HE02",
                total_uc_amount_cad=1200.0,
                total_charged_volume_mw=150.0,
            )
        ],
        datetime(2026, 8, 5, tzinfo=UTC),
        {"source_product": "UC Settlement Report"},
    )
    service = HistoryService(
        AsyncMock(),
        public_reports,
        AsyncMock(),
        _settings(),
        store=MagicMock(),
    )
    req = UnitCommitmentSettlementRequest(start_date=start_date, end_date=end_date)
    resp = await service.get_uc_settlement_summary(req)
    assert len(resp.intervals) == 1
    assert resp.intervals[0].total_uc_amount_cad == 1200.0
    public_reports.get_unit_commitment_settlement_summary.assert_awaited_once_with(
        start_date, end_date
    )

    # Over 366 days raises error
    req_invalid = UnitCommitmentSettlementRequest(
        start_date=date(2025, 1, 1), end_date=date(2026, 3, 1)
    )
    with pytest.raises(InvalidDateRangeError):
        await service.get_uc_settlement_summary(req_invalid)


@pytest.mark.asyncio
async def test_get_historical_store_status_enabled_and_disabled() -> None:
    store = MagicMock()
    service = HistoryService(
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        _settings(),
        store=store,
    )

    # Disabled
    store.dependencies_available.return_value = False
    disabled_status = await service.get_historical_store_status()
    assert disabled_status.enabled is False
    assert len(disabled_status.warnings) == 1

    from aeso_mcp.models.history import HistoricalDatasetStatus

    # Enabled
    store.dependencies_available.return_value = True
    store.statuses.return_value = [
        HistoricalDatasetStatus(dataset=HistoricalDataset.POOL_PRICE, observation_count=100)
    ]
    store.schema_version = 3
    enabled_status = await service.get_historical_store_status()
    assert enabled_status.enabled is True
    assert enabled_status.metadata.observation_count == 100


@pytest.mark.asyncio
async def test_sync_historical_store_generation_and_market_series() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)

    source = HistoricalGenerationSourceFile(
        file_id="f1",
        name="test.zip",
        size_bytes=1000,
        content_updated_at=start,
        source_hash="h1",
        coverage_start=start.date(),
        coverage_end=end.date(),
    )
    generation = AsyncMock()
    generation.list_source_files.return_value = [source]
    generation.fetch_source_file.return_value = ([_gen_interval(start)], "digest1")

    store = MagicMock()
    store.dependencies_available.return_value = True
    store.source_is_current.return_value = False
    store.replace_generation_source.return_value = (1, 1)
    store.missing_generation_intervals.return_value = 0
    store.missing_market_ranges.return_value = [(start, end)]
    store.upsert_market_series.return_value = (1, 1)
    store.upsert_forecast_vintages.return_value = None
    store.schema_version = 3
    store.root = "/tmp/history"

    market = AsyncMock()
    market.get_pool_prices.return_value = PoolPriceResponse(
        intervals=[
            PoolPriceInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                pool_price_cad_per_mwh=42.0,
                forecast_pool_price_cad_per_mwh=40.0,
            )
        ],
        page=_page(1),
        metadata=_meta("Pool Price", 1),
        warnings=[],
    )
    market.get_load.return_value = LoadResponse(
        intervals=[
            LoadInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                load_mw=9000.0,
                load_forecast_mw=8900.0,
            )
        ],
        page=_page(1),
        metadata=_meta("AIL", 1),
        warnings=[],
    )

    service = HistoryService(
        generation,
        AsyncMock(),
        market,
        _settings(),
        store=store,
    )

    sync_req = HistoricalStoreSyncRequest(
        start=start,
        end=end,
        datasets=[
            HistoricalDataset.HISTORICAL_GENERATION,
            HistoricalDataset.POOL_PRICE,
            HistoricalDataset.AIL,
        ],
    )
    sync_resp = await service.sync_historical_store(sync_req)
    assert len(sync_resp.results) == 3
    assert sync_resp.results[0].dataset == HistoricalDataset.HISTORICAL_GENERATION
    assert sync_resp.results[0].records_written == 1
    assert sync_resp.results[1].dataset == HistoricalDataset.POOL_PRICE
    assert sync_resp.results[1].records_written == 1
    assert sync_resp.results[2].dataset == HistoricalDataset.AIL
    assert sync_resp.results[2].records_written == 1
