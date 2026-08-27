# SPDX-License-Identifier: MIT
"""Focused tests for gross revenue, CSD comparisons, ramps, and HTTP startup policy."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from pydantic import SecretStr

from aeso_mcp.config import Settings
from aeso_mcp.errors import ConfigurationError
from aeso_mcp.http_runtime import build_http_runtime, validate_http_bind
from aeso_mcp.models.analytics import (
    AssetEnergyRevenueRequest,
    CsdMeteredComparisonRequest,
    ForecastErrorAnalyticsRequest,
    RampAnalysisRequest,
    SupplySurplusAnalysisRequest,
)
from aeso_mcp.models.common import DatasetMetadata, ProviderName
from aeso_mcp.models.generation import (
    GenerationInterval,
    GenerationResponse,
    LoadInterval,
    LoadResponse,
)
from aeso_mcp.models.history import (
    HistoricalGenerationInterval,
    HistoricalGenerationResponse,
)
from aeso_mcp.models.operations import (
    MeteredVolumeInterval,
    MeteredVolumeResponse,
    PageInfo,
)
from aeso_mcp.models.prices import PoolPriceInterval, PoolPriceResponse
from aeso_mcp.models.reports import (
    SupplySurplusEvent,
    SupplySurplusEventsResponse,
)
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.timeutil import MARKET_TZ


def _metadata(dataset: str) -> DatasetMetadata:
    now = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    return DatasetMetadata(
        dataset=dataset,
        source_product=dataset,
        retrieved_at=now,
        provider=ProviderName.DERIVED,
    )


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"aeso_api_key": SecretStr("test-key")}
    values.update(overrides)
    return Settings.model_validate(values)


def _page(total: int, returned: int) -> PageInfo:
    return PageInfo(offset=0, limit=2_000, returned=returned, total=total)


@pytest.mark.asyncio
async def test_asset_energy_revenue_is_a_matched_metered_price_join() -> None:
    market = AsyncMock()
    operations = AsyncMock()
    history = AsyncMock()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    prices = [
        PoolPriceInterval(
            interval_start=start + timedelta(hours=i),
            interval_end=start + timedelta(hours=i + 1),
            pool_price_cad_per_mwh=100.0 + 50.0 * i,
        )
        for i in range(2)
    ]
    price_response = PoolPriceResponse(
        intervals=prices,
        page=_page(2, 2),
        metadata=_metadata("Pool Price"),
    )
    history.get_historical_pool_prices.return_value = price_response
    operations.get_metered_volumes.return_value = MeteredVolumeResponse(
        intervals=[
            MeteredVolumeInterval(
                asset_id="ASSET-1",
                interval_start=start,
                metered_volume_mwh=2.0,
            )
        ],
        page=_page(1, 1),
        metadata=_metadata("Metered Volume"),
    )
    service = AnalyticsService(market, _settings(), operations=operations, history=history)

    result = await service.calculate_asset_energy_revenue(
        AssetEnergyRevenueRequest(
            start=start,
            end=start + timedelta(hours=2),
            asset_ids=["asset-1"],
        )
    )

    row = result.results[0]
    assert row.matched_mwh == 2.0
    assert row.gross_energy_revenue_cad == 200.0
    assert row.realized_price_cad_per_mwh == 100.0
    assert row.average_market_price_cad_per_mwh == 100.0
    assert row.capture_rate == 1.0
    assert row.matched_observations == 1
    assert row.missing_intervals == 1
    assert row.missing_metered_intervals == 1
    history.get_historical_pool_prices.assert_awaited_once()
    market.get_pool_prices.assert_not_awaited()


@pytest.mark.asyncio
async def test_csd_comparison_converts_mw_to_mwh_and_preserves_missing() -> None:
    market = AsyncMock()
    operations = AsyncMock()
    history = AsyncMock()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    history.get_historical_generation.return_value = HistoricalGenerationResponse(
        intervals=[
            HistoricalGenerationInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                interval_start_utc=start,
                interval_end_utc=start + timedelta(hours=1),
                resolution="hourly",
                asset_id="ASSET-1",
                fuel_type="WIND",
                generation_mw=10.0,
                source_file_id="file",
                source_file_name="file.csv",
                source_retrieved_at=start,
            )
        ],
        page=_page(1, 1),
        metadata=_metadata("Historical CSD"),
    )
    operations.get_metered_volumes.return_value = MeteredVolumeResponse(
        intervals=[
            MeteredVolumeInterval(
                asset_id="ASSET-1",
                interval_start=start,
                metered_volume_mwh=8.0,
            )
        ],
        page=_page(1, 1),
        metadata=_metadata("Metered Volume"),
    )
    service = AnalyticsService(market, _settings(), operations=operations, history=history)

    result = await service.compare_csd_to_metered(
        CsdMeteredComparisonRequest(
            start=start,
            end=start + timedelta(hours=2),
            asset_ids=["asset-1"],
        )
    )

    row = result.results[0]
    assert row.matched_hours == 1
    assert row.operational_generation_estimate_mwh == 10.0
    assert row.metered_energy_mwh == 8.0
    assert row.mean_signed_difference_mwh == 2.0
    assert row.mean_absolute_percentage_difference == 0.25
    assert row.missing_csd_hours == 1
    assert row.missing_metered_hours == 1


@pytest.mark.asyncio
async def test_ramp_analysis_does_not_bridge_a_missing_hour() -> None:
    market = AsyncMock()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    market.get_load.return_value = LoadResponse(
        intervals=[
            LoadInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                load_mw=100.0,
            ),
            LoadInterval(
                interval_start=start + timedelta(hours=2),
                interval_end=start + timedelta(hours=3),
                load_mw=140.0,
            ),
        ],
        page=_page(2, 2),
        metadata=_metadata("AIL"),
    )
    service = AnalyticsService(market, _settings())

    result = await service.analyze_ramps(
        RampAnalysisRequest(
            start=start,
            end=start + timedelta(hours=3),
            series="ail",
            cadence="hourly",
        )
    )

    assert result.observation_count == 2
    assert result.ramp_observation_count == 0
    assert result.maximum_up_ramp_mw is None
    assert any("non-consecutive" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_forecast_error_supports_pool_price_without_fabricating_missing_forecasts() -> None:
    market = AsyncMock()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    market.get_pool_prices.return_value = PoolPriceResponse(
        intervals=[
            PoolPriceInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                pool_price_cad_per_mwh=100.0,
                forecast_pool_price_cad_per_mwh=110.0,
            ),
            PoolPriceInterval(
                interval_start=start + timedelta(hours=1),
                interval_end=start + timedelta(hours=2),
                pool_price_cad_per_mwh=200.0,
            ),
        ],
        page=_page(2, 2),
        metadata=_metadata("Forecast and Actual Pool Price"),
    )
    service = AnalyticsService(market, _settings())

    result = await service.analyze_forecast_error(
        ForecastErrorAnalyticsRequest(
            start=start,
            end=start + timedelta(hours=2),
            series="pool_price",
        )
    )

    assert result.observation_count == 1
    assert result.mean_error == 10.0
    assert result.mean_absolute_error == 10.0
    assert result.missing_forecast_count == 1
    assert any("without forecasts" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_supply_surplus_analysis_uses_only_explicit_event_end_and_observed_series() -> None:
    market = AsyncMock()
    history = AsyncMock()
    reports = AsyncMock()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    reports.get_supply_surplus_events.return_value = SupplySurplusEventsResponse(
        events=[
            SupplySurplusEvent(
                start=start,
                end=start + timedelta(hours=2),
                status="all_zero_forecast_prices",
                status_code=0,
                source_confidence="published_status",
            ),
            SupplySurplusEvent(
                start=start + timedelta(hours=2),
                status="some_zero_forecast_prices",
                status_code=1,
                source_confidence="published_status",
            ),
        ],
        page=_page(2, 2),
        metadata=_metadata("Supply Surplus Events"),
    )
    history.get_historical_pool_prices.return_value = PoolPriceResponse(
        intervals=[
            PoolPriceInterval(
                interval_start=start + timedelta(hours=index),
                interval_end=start + timedelta(hours=index + 1),
                pool_price_cad_per_mwh=value,
            )
            for index, value in enumerate((0.0, 10.0, 20.0))
        ],
        page=_page(3, 3),
        metadata=_metadata("Pool Price"),
    )
    history.get_historical_load.return_value = LoadResponse(
        intervals=[
            LoadInterval(
                interval_start=start + timedelta(hours=index),
                interval_end=start + timedelta(hours=index + 1),
                load_mw=10_000.0 + 100 * index,
            )
            for index in range(3)
        ],
        page=_page(3, 3),
        metadata=_metadata("AIL"),
    )
    market.get_generation.return_value = GenerationResponse(
        intervals=[
            GenerationInterval(
                interval_start=start + timedelta(hours=index),
                interval_end=start + timedelta(hours=index + 1),
                fuel_type="Wind",
                generation_mw=1_000.0 + index,
            )
            for index in range(3)
        ],
        page=_page(3, 3),
        metadata=_metadata("Wind Generation"),
    )
    service = AnalyticsService(
        market,
        _settings(),
        history=history,
        reports=reports,
    )

    result = await service.analyze_supply_surplus_events(
        SupplySurplusAnalysisRequest(start=start, end=start + timedelta(hours=3))
    )

    assert result.event_count == 2
    assert result.explicitly_bounded_event_count == 1
    assert result.total_explicit_duration_hours == 2.0
    assert result.events[0].average_pool_price_cad_per_mwh == 5.0
    assert result.events[1].duration_hours is None
    assert result.events[1].average_pool_price_cad_per_mwh == 20.0
    assert "historical_interchange" in result.metadata.missing_series
    assert any("do not establish causation" in warning for warning in result.warnings)


def test_remote_http_bind_requires_auth_by_default() -> None:
    settings = _settings()
    with pytest.raises(ConfigurationError, match="Non-loopback HTTP binding requires"):
        validate_http_bind(settings, "public.example")
    validate_http_bind(settings, "127.0.0.1")
    validate_http_bind(_settings(http_allow_insecure_remote=True), "public.example")
    validate_http_bind(
        _settings(http_bearer_token=SecretStr("remote-token")),
        "public.example",
    )


def test_build_http_runtime_keeps_loopback_compatibility() -> None:
    mcp = FastMCP(name="runtime-test")
    runtime = build_http_runtime(mcp, _settings(), host="127.0.0.1")
    assert runtime.host_origin_protection is True
