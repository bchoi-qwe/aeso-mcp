# SPDX-License-Identifier: MIT
"""Behavioral coverage for cadence and forecast analytics boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.errors import UnsupportedDatasetError
from aeso_mcp.models.analytics import ForecastErrorAnalyticsRequest, RampAnalysisRequest
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.timeutil import MARKET_TZ


def _response(intervals: list[object], warnings: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(intervals=intervals, warnings=warnings or [])


def _service(
    market: AsyncMock | None = None,
    *,
    history: AsyncMock | None = None,
    forecasts: AsyncMock | None = None,
) -> tuple[AnalyticsService, AsyncMock]:
    market = market or AsyncMock()
    settings = Settings(aeso_api_key="test-key")  # type: ignore[arg-type]
    return AnalyticsService(market, settings, history=history, forecasts=forecasts), market


@pytest.mark.asyncio
async def test_ramps_support_net_load_and_skip_unobserved_renewables() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    market = AsyncMock()
    market.get_load.return_value = _response(
        [
            SimpleNamespace(interval_start=start, load_mw=100.0),
            SimpleNamespace(interval_start=start + timedelta(hours=1), load_mw=120.0),
            SimpleNamespace(interval_start=start + timedelta(hours=2), load_mw=140.0),
        ]
    )
    market.get_generation.return_value = _response(
        [
            SimpleNamespace(interval_start=start, fuel_type="WIND", generation_mw=20.0),
            SimpleNamespace(
                interval_start=start + timedelta(hours=1), fuel_type="SOLAR", generation_mw=30.0
            ),
            SimpleNamespace(
                interval_start=start + timedelta(hours=2), fuel_type="COAL", generation_mw=50.0
            ),
        ]
    )
    service, _ = _service(market)

    result = await service.analyze_ramps(
        RampAnalysisRequest(
            start=start,
            end=start + timedelta(hours=3),
            series="net_load",
            percentiles=[0, 50, 100],
        )
    )

    assert result.observation_count == 2
    assert result.ramp_observation_count == 1
    assert result.largest_ramps[0].delta_mw == 10
    assert result.ramp_percentiles_mw == {"0.0": 10.0, "50.0": 10.0, "100.0": 10.0}
    assert any("exclude hours" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_hourly_wind_ramps_filter_fuel_type_and_aggregate_duplicate_rows() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    market = AsyncMock()
    market.get_generation.return_value = _response(
        [
            SimpleNamespace(interval_start=start, fuel_type="WIND", generation_mw=10.0),
            SimpleNamespace(interval_start=start, fuel_type="WIND", generation_mw=5.0),
            SimpleNamespace(
                interval_start=start + timedelta(hours=1), fuel_type="WIND", generation_mw=30.0
            ),
            SimpleNamespace(interval_start=start, fuel_type="SOLAR", generation_mw=100.0),
        ]
    )
    service, _ = _service(market)

    result = await service.analyze_ramps(
        RampAnalysisRequest(start=start, end=start + timedelta(hours=2), series="wind")
    )

    assert result.observation_count == 2
    assert result.ramp_observation_count == 1
    assert result.largest_ramps[0].from_value == 15
    assert result.largest_ramps[0].to_value == 30
    assert result.largest_ramps[0].direction == "up"


@pytest.mark.asyncio
async def test_asset_ramps_use_historical_csd_at_five_minute_cadence() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    history = AsyncMock()
    history.get_historical_generation.return_value = _response(
        [
            SimpleNamespace(interval_start=start, generation_mw=5.0, fuel_type="GAS"),
            SimpleNamespace(
                interval_start=start + timedelta(minutes=5), generation_mw=8.0, fuel_type="GAS"
            ),
        ],
        ["historical CSD warning"],
    )
    service, market = _service(history=history)

    result = await service.analyze_ramps(
        RampAnalysisRequest(
            start=start,
            end=start + timedelta(minutes=10),
            series="asset",
            cadence="5-minute",
            asset_ids=["GEN1"],
        )
    )

    assert result.ramp_observation_count == 1
    assert result.cadence_minutes == 5
    assert result.largest_ramps[0].ramp_rate_mw_per_hour == pytest.approx(36)
    assert result.warnings == ["historical CSD warning"]
    market.get_generation.assert_not_awaited()
    history.get_historical_generation.assert_awaited_once()


@pytest.mark.asyncio
async def test_ramp_rejects_unsupported_five_minute_ail_and_missing_history() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    service, market = _service()

    with pytest.raises(UnsupportedDatasetError, match="Five-minute ramp"):
        await service.analyze_ramps(
            RampAnalysisRequest(
                start=start,
                end=start + timedelta(minutes=10),
                series="ail",
                cadence="5-minute",
            )
        )
    with pytest.raises(UnsupportedDatasetError, match="historical CSD"):
        await service.analyze_ramps(
            RampAnalysisRequest(
                start=start,
                end=start + timedelta(minutes=10),
                series="asset",
                cadence="5-minute",
                asset_ids=["GEN1"],
            )
        )
    market.get_load.assert_not_awaited()


@pytest.mark.asyncio
async def test_official_forecast_errors_preserve_missing_counts_and_lead_times() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    forecasts = AsyncMock()
    forecasts.get_forecast.return_value = _response(
        [
            SimpleNamespace(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                actual_value=100.0,
                forecast_value=110.0,
                lead_time_hours=6,
                lead_time_minutes=None,
            ),
            SimpleNamespace(
                interval_start=start + timedelta(hours=1),
                interval_end=start + timedelta(hours=2),
                actual_value=None,
                forecast_value=120.0,
                lead_time_hours=None,
                lead_time_minutes=30,
            ),
            SimpleNamespace(
                interval_start=start + timedelta(hours=2),
                interval_end=start + timedelta(hours=3),
                actual_value=0.0,
                forecast_value=None,
                lead_time_hours=8,
                lead_time_minutes=None,
            ),
        ]
    )
    service, _ = _service(forecasts=forecasts)

    result = await service.analyze_forecast_error(
        ForecastErrorAnalyticsRequest(
            start=start,
            end=start + timedelta(hours=3),
            series="wind",
            percentiles=[0, 50, 100],
        )
    )

    assert result.observation_count == 1
    assert result.mean_error == 10
    assert result.mean_absolute_percentage_error == pytest.approx(0.1)
    assert result.missing_actual_count == 1
    assert result.missing_forecast_count == 1
    assert result.intervals[0].lead_time_hours == 6
    assert result.by_lead_time[0].lead_time_hours == 6
    assert any("without actuals" in warning for warning in result.warnings)
    assert any("without forecasts" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_historical_ail_forecast_errors_skip_unpaired_rows() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    history = AsyncMock()
    history.get_forecast.return_value = _response(
        [
            SimpleNamespace(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                actual_value=100.0,
                forecast_value=90.0,
                lead_time_hours=2,
            ),
            SimpleNamespace(
                interval_start=start + timedelta(hours=1),
                interval_end=start + timedelta(hours=2),
                actual_value=110.0,
                forecast_value=None,
                lead_time_hours=None,
            ),
        ],
        ["stored forecast warning"],
    )
    service, market = _service(history=history)

    result = await service.analyze_forecast_error(
        ForecastErrorAnalyticsRequest(
            start=start,
            end=start + timedelta(hours=2),
            series="ail",
        )
    )

    assert result.observation_count == 1
    assert result.mean_error == -10
    assert result.missing_forecast_count == 1
    assert result.by_lead_time[0].lead_time_hours == 2
    assert "stored forecast warning" in result.warnings
    market.get_load.assert_not_awaited()
    history.get_forecast.assert_awaited_once()
