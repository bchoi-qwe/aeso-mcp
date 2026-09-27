# SPDX-License-Identifier: MIT
"""Service tests for official forecasts and named operational reports."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.errors import UnsupportedDatasetError
from aeso_mcp.models.forecasts import ForecastInterval, OfficialForecastRequest
from aeso_mcp.models.reports import (
    DdsAvailabilityRecord,
    DdsMarketReportRequest,
    FfrNetScheduleInterval,
    FfrNetScheduleRequest,
    SupplyAdequacyInterval,
    SupplyAdequacyRequest,
    SupplySurplusEventsRequest,
    SupplySurplusInterval,
    SupplySurplusRequest,
    SystemEvent,
    SystemEventsRequest,
    TmrReferencePrice,
    TmrReferencePriceRequest,
)
from aeso_mcp.providers.base import AesoDataProvider
from aeso_mcp.providers.forecasts import AesoForecastProvider
from aeso_mcp.providers.reports import AesoReportsProvider
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.forecasts import ForecastService, summarize_forecast_error
from aeso_mcp.services.reports import ReportsService
from aeso_mcp.timeutil import MARKET_TZ


def _settings() -> Settings:
    return Settings(aeso_api_key="test-key")  # type: ignore[arg-type]


def _provenance(product: str) -> dict[str, object]:
    return {"provider": "aeso_public_report", "source_product": product}


@pytest.mark.asyncio
async def test_dds_report_empty_half_open_range_has_consistent_metadata() -> None:
    provider = AsyncMock()
    start = datetime(2026, 8, 26, 12, tzinfo=MARKET_TZ)
    provider.get_dds_market_report.return_value = (
        [
            DdsAvailabilityRecord(observed_at=start - timedelta(seconds=1), available_dds_mw=25.0),
            DdsAvailabilityRecord(observed_at=start + timedelta(hours=1), available_dds_mw=30.0),
        ],
        None,
        _provenance("Dispatch Down Service Market Report"),
    )
    service = ReportsService(cast(AesoReportsProvider, provider), _settings(), AsyncTTLCache())

    response = await service.get_dds_market_report(
        DdsMarketReportRequest(start=start, end=start + timedelta(hours=1))
    )

    assert response.records == []
    assert response.page.total == response.metadata.observation_count == 0
    assert response.page.returned == 0
    assert response.metadata.completeness.value == "empty"


@pytest.mark.asyncio
async def test_forecast_service_routes_shared_series_contract_and_paginates() -> None:
    provider = AsyncMock()
    ail_provider = AsyncMock()
    start = datetime(2026, 8, 26, 12, tzinfo=MARKET_TZ)
    wind = [
        ForecastInterval(
            interval_start=start + timedelta(minutes=10 * index),
            interval_end=start + timedelta(minutes=10 * (index + 1)),
            series="wind",
            horizon="current_12_hour",
            forecast_value=100.0 + index,
            actual_value=98.0 + index,
            unit="MW",
            source_product="Wind short-term forecast",
        )
        for index in range(3)
    ]
    provider.get_current_forecast.return_value = (
        wind,
        start - timedelta(minutes=5),
        _provenance("Wind short-term forecast"),
    )
    service = ForecastService(
        cast(AesoForecastProvider, provider),
        _settings(),
        AsyncTTLCache(),
        ail_provider=cast(AesoDataProvider, ail_provider),
    )

    response = await service.get_forecast(
        OfficialForecastRequest(
            start=start,
            end=start + timedelta(minutes=30),
            series="wind",
            horizon="current_12_hour",
            include_actual=False,
            offset=1,
            limit=1,
        )
    )

    assert response.page.total == 3
    assert response.page.returned == 1
    assert response.intervals[0].forecast_value == 101.0
    assert response.intervals[0].actual_value is None
    assert response.metadata.observation_granularity == "10m"
    assert response.metadata.provider.value == "aeso_public_report"


@pytest.mark.asyncio
async def test_forecast_service_handles_pool_price_ail_and_unsupported_combined_history() -> None:
    provider = AsyncMock()
    ail_provider = AsyncMock()
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    pool = ForecastInterval(
        interval_start=start,
        interval_end=start + timedelta(hours=1),
        series="pool_price",
        horizon="historical",
        forecast_value=40.0,
        actual_value=42.0,
        unit="CAD/MWh",
        source_product="Forecast and Actual Pool Price",
    )
    provider.get_pool_price_forecast.return_value = (
        [pool],
        start,
        _provenance("Forecast and Actual Pool Price"),
    )
    ail_provider.get_load.return_value = (
        [
            {
                "interval_start": start,
                "interval_end": start + timedelta(hours=1),
                "load_mw": 10_000.0,
                "load_forecast_mw": 9_950.0,
            }
        ],
        {"provider": "gridstatus", "source_product": "Actual Forecast Report"},
    )
    service = ForecastService(
        cast(AesoForecastProvider, provider),
        _settings(),
        ail_provider=cast(AesoDataProvider, ail_provider),
    )

    pool_response = await service.get_forecast(
        OfficialForecastRequest(
            start=start,
            end=start + timedelta(hours=1),
            series="pool_price",
        )
    )
    ail_response = await service.get_forecast(
        OfficialForecastRequest(
            start=start,
            end=start + timedelta(hours=1),
            series="ail",
        )
    )

    assert pool_response.intervals[0].actual_value == 42.0
    assert ail_response.intervals[0].forecast_value == 9_950.0
    with pytest.raises(UnsupportedDatasetError, match="separately"):
        await service.get_forecast(
            OfficialForecastRequest(
                start=start,
                end=start + timedelta(hours=1),
                series="wind_solar",
                horizon="historical",
            )
        )


def test_forecast_error_summary_counts_unpaired_values_and_zero_denominators() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    rows = [
        ForecastInterval(
            interval_start=start,
            interval_end=start + timedelta(hours=1),
            series="solar",
            horizon="historical",
            forecast_value=12.0,
            actual_value=10.0,
            unit="MW",
            source_product="Solar historical",
        ),
        ForecastInterval(
            interval_start=start + timedelta(hours=1),
            interval_end=start + timedelta(hours=2),
            series="solar",
            horizon="historical",
            forecast_value=1.0,
            actual_value=0.0,
            unit="MW",
            source_product="Solar historical",
        ),
        ForecastInterval(
            interval_start=start + timedelta(hours=2),
            interval_end=start + timedelta(hours=3),
            series="solar",
            horizon="historical",
            forecast_value=None,
            actual_value=5.0,
            unit="MW",
            source_product="Solar historical",
        ),
    ]

    summary = summarize_forecast_error(rows, series="solar")

    assert summary.observation_count == 2
    assert summary.excluded_without_forecast == 1
    assert summary.mape == 20.0
    assert summary.mean_error == 1.5


@pytest.mark.asyncio
async def test_reports_service_preserves_each_named_report_contract() -> None:
    provider = AsyncMock()
    start = datetime(2026, 8, 26, tzinfo=MARKET_TZ)
    provenance = _provenance("fixture")
    provider.get_supply_adequacy.return_value = (
        [
            SupplyAdequacyInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                adequacy_status_code=3,
                adequacy_status="200_to_400_mw_supply_available",
                supply_cushion_code=4,
                supply_cushion_status="above_600_to_800_mw",
            )
        ],
        start,
        provenance,
    )
    surplus_rows = [
        SupplySurplusInterval(
            interval_start=start + timedelta(hours=index),
            interval_end=start + timedelta(hours=index + 1),
            status_code=code,
            status=("all_zero_forecast_prices" if code == 0 else "all_forecast_prices_positive"),
        )
        for index, code in enumerate((0, 0, 2))
    ]
    provider.get_supply_surplus.return_value = (surplus_rows, start, provenance)
    provider.get_ffr_net_schedule.return_value = (
        [
            FfrNetScheduleInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                net_schedule_mw=-100.0,
            )
        ],
        start,
        provenance,
    )
    provider.get_dds_market_report.return_value = (
        [DdsAvailabilityRecord(observed_at=start, available_dds_mw=25.0)],
        start,
        provenance,
    )
    provider.get_tmr_reference_price.return_value = (
        [TmrReferencePrice(effective_date=date(2026, 8, 1), reference_price_cad_per_mwh=42.0)],
        start,
        provenance,
    )
    provider.get_system_events.return_value = (
        [
            SystemEvent(
                event_time=start, comments="Supply surplus entered", event_type="supply_surplus"
            )
        ],
        start,
        provenance,
    )
    service = ReportsService(cast(AesoReportsProvider, provider), _settings(), AsyncTTLCache())

    adequacy = await service.get_supply_adequacy(SupplyAdequacyRequest())
    surplus = await service.get_supply_surplus(
        SupplySurplusRequest(start=start, end=start + timedelta(hours=3))
    )
    surplus_events = await service.get_supply_surplus_events(
        SupplySurplusEventsRequest(start=start, end=start + timedelta(hours=3))
    )
    ffr = await service.get_ffr_net_schedule(
        FfrNetScheduleRequest(start=start, end=start + timedelta(hours=1))
    )
    dds = await service.get_dds_market_report(
        DdsMarketReportRequest(start=start, end=start + timedelta(hours=1))
    )
    tmr = await service.get_tmr_reference_price(
        TmrReferencePriceRequest(start_date=date(2026, 8, 1), end_date=date(2026, 8, 31))
    )
    events = await service.get_system_events(
        SystemEventsRequest(start=start, end=start + timedelta(hours=1))
    )

    assert adequacy.intervals[0].supply_cushion_code == 4
    assert len(surplus.intervals) == 3
    assert surplus_events.events[0].end == surplus_rows[2].interval_start
    assert ffr.intervals[0].net_schedule_mw == -100.0
    assert dds.records[0].available_dds_mw == 25.0
    assert tmr.records[0].reference_price_cad_per_mwh == 42.0
    assert events.records[0].event_type == "supply_surplus"
