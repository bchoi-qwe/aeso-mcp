# SPDX-License-Identifier: MIT
"""Multivariate research-service behavior."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from aeso_mcp.config import Settings
from aeso_mcp.errors import AuthenticationError, InvalidDateRangeError
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    FinalityStatus,
    ObservationType,
    ProviderName,
)
from aeso_mcp.models.forecasts import OfficialForecastRequest
from aeso_mcp.models.operations import EnergyMeritOrderBlock, PageInfo
from aeso_mcp.models.prices import PoolPriceInterval, PoolPriceResponse
from aeso_mcp.models.research import (
    AssetDispatchRequest,
    CapturePriceRequest,
    ForecastErrorRequest,
    GenerationAnalysisRequest,
    IntertieUtilizationRequest,
    MarketEventRequest,
    NetLoadRequest,
    OutageImpactRequest,
    PriceDurationCurveRequest,
    PriceStatisticsRequest,
    SupplyStackRequest,
)
from aeso_mcp.services.grid import GridService
from aeso_mcp.services.history import HistoryService
from aeso_mcp.services.market import MarketService
from aeso_mcp.services.operations import OperationsService
from aeso_mcp.services.research import ResearchService
from aeso_mcp.services.reserves import OperatingReserveService
from aeso_mcp.timeutil import MARKET_TZ


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_renewables", [False, True])
async def test_market_event_returns_structured_multivariate_evidence(missing_renewables) -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    second = start + timedelta(hours=1)
    market = SimpleNamespace(
        get_pool_prices=AsyncMock(
            return_value=SimpleNamespace(
                intervals=[
                    SimpleNamespace(interval_start=start, pool_price_cad_per_mwh=50.0),
                    SimpleNamespace(interval_start=second, pool_price_cad_per_mwh=150.0),
                ],
                warnings=[],
            )
        ),
        get_load=AsyncMock(
            return_value=SimpleNamespace(
                intervals=[
                    SimpleNamespace(
                        interval_start=start, load_mw=9_000.0, load_forecast_mw=8_900.0
                    ),
                    SimpleNamespace(
                        interval_start=second, load_mw=9_200.0, load_forecast_mw=9_100.0
                    ),
                ],
                warnings=[],
            )
        ),
    )
    history = SimpleNamespace(
        get_historical_pool_prices=market.get_pool_prices,
        get_historical_load=market.get_load,
        get_historical_generation=AsyncMock(
            return_value=SimpleNamespace(
                intervals=[
                    SimpleNamespace(
                        interval_start_utc=start,
                        fuel_type="WIND",
                        generation_mw=1_000.0,
                    ),
                    SimpleNamespace(
                        interval_start_utc=start,
                        fuel_type="GAS",
                        generation_mw=4_000.0,
                    ),
                    SimpleNamespace(
                        interval_start_utc=second,
                        fuel_type="WIND",
                        generation_mw=800.0,
                    ),
                    SimpleNamespace(
                        interval_start_utc=second,
                        fuel_type="GAS",
                        generation_mw=4_200.0,
                    ),
                ],
                warnings=[],
            )
        ),
    )
    grid = SimpleNamespace(
        get_outages=AsyncMock(
            return_value=SimpleNamespace(
                outages=[
                    SimpleNamespace(interval_start=start, total_outage_mw=500.0),
                    SimpleNamespace(interval_start=second, total_outage_mw=700.0),
                ],
                warnings=[],
            )
        )
    )
    operations = SimpleNamespace(
        get_generation_capacity=AsyncMock(
            return_value=SimpleNamespace(
                intervals=[
                    SimpleNamespace(interval_start=start, available_capability_mw=12_000.0),
                    SimpleNamespace(interval_start=second, available_capability_mw=11_800.0),
                ],
                warnings=[],
            )
        ),
        get_intertie_capability=AsyncMock(
            return_value=SimpleNamespace(
                intervals=[
                    SimpleNamespace(
                        available_transfer_capability_mw=1_000.0,
                        gross_offer_mw=900.0,
                        direction="import",
                    )
                ],
                warnings=[],
            )
        ),
        get_intertie_outages=AsyncMock(
            return_value=SimpleNamespace(outages=[SimpleNamespace()], warnings=[])
        ),
        get_unit_commitments=AsyncMock(
            return_value=SimpleNamespace(directives=[SimpleNamespace()], warnings=[])
        ),
        get_energy_merit_order=AsyncMock(
            return_value=SimpleNamespace(
                blocks=[
                    SimpleNamespace(
                        asset_id="ASSET-A",
                        block_size_mw=100.0,
                        from_mw=0.0,
                        to_mw=100.0,
                        dispatched_mw=50.0,
                        block_price_cad_per_mwh=75.0,
                    ),
                    SimpleNamespace(
                        asset_id="ASSET-B",
                        block_size_mw=300.0,
                        from_mw=100.0,
                        to_mw=400.0,
                        dispatched_mw=10.0,
                        block_price_cad_per_mwh=125.0,
                    ),
                ],
                warnings=[],
            )
        ),
        get_operating_reserve_offer_control=AsyncMock(
            return_value=SimpleNamespace(blocks=[SimpleNamespace()], warnings=[])
        ),
    )
    reserves = SimpleNamespace(
        summarize=AsyncMock(
            return_value=SimpleNamespace(
                results=[
                    SimpleNamespace(
                        procurement="active",
                        average_price_cad_per_mw=20.0,
                        average_volume_mw=200.0,
                        activated_volume_mw=0.0,
                    ),
                    SimpleNamespace(
                        procurement="standby",
                        average_price_cad_per_mw=30.0,
                        average_volume_mw=50.0,
                        activated_volume_mw=5.0,
                    ),
                ],
                warnings=[],
            )
        )
    )
    service = ResearchService(
        cast(MarketService, market),
        cast(HistoryService, history),
        cast(OperationsService, operations),
        cast(GridService, grid),
        cast(OperatingReserveService, reserves),
        Settings(aeso_api_key=SecretStr("research-test-key")),
    )

    if missing_renewables:
        generation = history.get_historical_generation.return_value
        generation.intervals = [
            item
            for item in generation.intervals
            if not (item.fuel_type == "WIND" and item.interval_start_utc == second)
        ]
    response = await service.analyze_market_event(
        MarketEventRequest(
            start=start,
            end=start + timedelta(hours=2),
            baseline_start=start - timedelta(hours=2),
            baseline_end=start,
        )
    )

    assert response.evidence.price.average_cad_per_mwh == 100
    assert response.evidence.price.high_price_hours == 1
    assert response.evidence.demand.forecast_mae_mw == 100
    assert response.evidence.supply.renewable_generation_share == pytest.approx(
        1000 / 9200 if missing_renewables else 0.18
    )
    assert response.evidence.supply.average_net_load_mw == (8000 if missing_renewables else 8200)
    assert (
        any("without renewable observations" in item for item in response.warnings)
        == missing_renewables
    )
    assert response.evidence.merit_order.marginal_dispatched_offer_cad_per_mwh == 125
    assert response.evidence.merit_order.offer_volume_hhi == pytest.approx(0.625)
    assert response.evidence.interties.average_offer_to_capability_ratio == 0.9
    assert response.evidence.interties.average_import_gross_offer_mw == 900
    assert response.evidence.reserves.average_active_volume_mw == 200
    assert response.evidence.reserves.standby_activated_volume_mw == 5
    assert response.evidence.reserves.offer_control_block_count == 1
    assert response.evidence.commitment_count == 1
    assert "do not establish" in response.methodology
    assert response.analysis_manifest.analysis_id.startswith("sha256:")
    assert response.analysis_manifest.methodology_version == "market-event-v2"
    assert {source.role for source in response.analysis_manifest.sources} == {
        "focus",
        "baseline",
    }
    assert (
        response.analysis_manifest.parameters["focus_start"]
        == start.astimezone(MARKET_TZ).isoformat()
    )
    assert history.get_historical_pool_prices.await_count == 2
    assert history.get_historical_load.await_count == 2


@pytest.mark.asyncio
async def test_optional_event_source_warns_but_authentication_fails_closed() -> None:
    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, SimpleNamespace()),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("research-test-key")),
    )
    warnings: list[str] = []

    async def unavailable() -> None:
        raise InvalidDateRangeError("not published for this date")

    async def unauthenticated() -> None:
        raise AuthenticationError("credentials rejected")

    assert await service._optional_event_source("merit order", unavailable(), warnings) is None
    assert warnings == ["merit order unavailable: not published for this date"]
    with pytest.raises(AuthenticationError, match="credentials rejected"):
        await service._optional_event_source("capacity", unauthenticated(), warnings)


@pytest.mark.asyncio
async def test_official_event_evidence_combines_forecasts_and_named_reports() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(hours=2)

    def forecast_response(request: OfficialForecastRequest, *, paginate: bool) -> SimpleNamespace:
        assert paginate is False
        series = request.series
        unit = "CAD/MWh" if series == "pool_price" else "MW"
        return SimpleNamespace(
            intervals=[
                SimpleNamespace(
                    forecast_value=12.0,
                    actual_value=10.0,
                    unit=unit,
                ),
                SimpleNamespace(
                    forecast_value=15.0,
                    actual_value=14.0,
                    unit=unit,
                ),
            ],
            warnings=[],
        )

    forecasts = SimpleNamespace(get_forecast=AsyncMock(side_effect=forecast_response))
    reports = SimpleNamespace(
        get_supply_adequacy=AsyncMock(
            return_value=SimpleNamespace(
                intervals=[
                    SimpleNamespace(
                        adequacy_status="adequate",
                        supply_cushion_status="above_600_mw",
                    )
                ],
                warnings=[],
            )
        ),
        get_supply_surplus=AsyncMock(
            return_value=SimpleNamespace(
                intervals=[SimpleNamespace(status="all_zero_forecast_prices")],
                warnings=[],
            )
        ),
        get_ffr_net_schedule=AsyncMock(
            return_value=SimpleNamespace(
                intervals=[
                    SimpleNamespace(net_schedule_mw=-100.0),
                    SimpleNamespace(net_schedule_mw=-50.0),
                ],
                warnings=[],
            )
        ),
        get_dds_market_report=AsyncMock(
            return_value=SimpleNamespace(
                records=[SimpleNamespace(available_dds_mw=25.0)], warnings=[]
            )
        ),
        get_tmr_reference_price=AsyncMock(
            return_value=SimpleNamespace(
                records=[SimpleNamespace(reference_price_cad_per_mwh=42.0)],
                warnings=[],
            )
        ),
        get_system_events=AsyncMock(
            return_value=SimpleNamespace(
                records=[SimpleNamespace(comments="Supply surplus entered")],
                warnings=[],
            )
        ),
    )
    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, SimpleNamespace()),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("research-test-key")),
        forecasts=forecasts,
        reports=reports,
    )
    warnings: list[str] = []

    evidence = await service._official_event_evidence(start, end, warnings)

    assert evidence.forecast_mae_by_series == {
        "pool_price": 1.5,
        "solar": 1.5,
        "wind": 1.5,
    }
    assert evidence.supply_adequacy_statuses == ["adequate"]
    assert evidence.supply_cushion_statuses == ["above_600_mw"]
    assert evidence.supply_surplus_statuses == ["all_zero_forecast_prices"]
    assert evidence.average_ffr_net_schedule_mw == -75.0
    assert evidence.average_dds_available_mw == 25.0
    assert evidence.tmr_reference_price_cad_per_mwh == 42.0
    assert evidence.system_event_comments == ["Supply surplus entered"]
    assert warnings == []
    assert forecasts.get_forecast.await_count == 3


def _pool_resp(intervals: list[PoolPriceInterval]) -> PoolPriceResponse:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return PoolPriceResponse(
        intervals=intervals,
        page=PageInfo(
            offset=0,
            limit=500,
            returned=len(intervals),
            total=len(intervals),
        ),
        metadata=DatasetMetadata(
            dataset="Pool Price",
            retrieved_at=now,
            observation_count=len(intervals),
            observation_granularity="1h",
            provider=ProviderName.AESO_APIM,
            observation_type=ObservationType.ACTUAL,
            completeness=DataCompleteness.COMPLETE,
            finality=FinalityStatus.FINAL,
        ),
        warnings=[],
    )


@pytest.mark.asyncio
async def test_get_price_duration_curve_empty_single_and_multiple() -> None:
    history = AsyncMock()
    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, history),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)

    # 1. Empty observations
    history.get_historical_pool_prices.return_value = _pool_resp([])
    resp_empty = await service.get_price_duration_curve(
        PriceDurationCurveRequest(start=start, end=end, points=10)
    )
    assert resp_empty.observation_count == 0
    assert resp_empty.points == []

    # 2. Single observation (verifies ZeroDivisionError fix)
    interval_single = PoolPriceInterval(
        interval_start=start,
        interval_end=start + timedelta(hours=1),
        pool_price_cad_per_mwh=45.0,
    )
    history.get_historical_pool_prices.return_value = _pool_resp([interval_single])
    resp_single = await service.get_price_duration_curve(
        PriceDurationCurveRequest(start=start, end=end, points=10)
    )
    assert resp_single.observation_count == 1
    assert len(resp_single.points) == 1
    assert resp_single.points[0].pool_price_cad_per_mwh == 45.0
    assert resp_single.points[0].exceedance_percent == 100.0

    # 3. Multiple observations
    intervals_multi = [
        PoolPriceInterval(
            interval_start=start + timedelta(hours=i),
            interval_end=start + timedelta(hours=i + 1),
            pool_price_cad_per_mwh=float(10 * (i + 1)),
        )
        for i in range(5)
    ]
    history.get_historical_pool_prices.return_value = _pool_resp(intervals_multi)
    resp_multi = await service.get_price_duration_curve(
        PriceDurationCurveRequest(start=start, end=end, points=3)
    )
    assert resp_multi.observation_count == 5
    assert len(resp_multi.points) <= 3
    assert resp_multi.points[0].pool_price_cad_per_mwh == 50.0


@pytest.mark.asyncio
async def test_get_price_statistics_calculates_percentiles_and_bounds() -> None:
    history = AsyncMock()
    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, history),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)

    prices = [10.0, 20.0, 30.0, 40.0, 50.0]
    intervals = [
        PoolPriceInterval(
            interval_start=start + timedelta(hours=i),
            interval_end=start + timedelta(hours=i + 1),
            pool_price_cad_per_mwh=p,
        )
        for i, p in enumerate(prices)
    ]
    history.get_historical_pool_prices.return_value = _pool_resp(intervals)
    resp = await service.get_price_statistics(
        PriceStatisticsRequest(start=start, end=end, percentiles=[50, 90])
    )
    assert resp.observation_count == 5
    assert resp.average_cad_per_mwh == 30.0
    assert resp.median_cad_per_mwh == 30.0
    assert resp.minimum_cad_per_mwh == 10.0
    assert resp.maximum_cad_per_mwh == 50.0
    assert len(resp.percentiles) == 2


@pytest.mark.asyncio
async def test_calculate_capture_prices_by_asset_and_fuel() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    history = AsyncMock()

    prices = [
        PoolPriceInterval(
            interval_start=start,
            interval_end=start + timedelta(hours=1),
            pool_price_cad_per_mwh=100.0,
        ),
        PoolPriceInterval(
            interval_start=start + timedelta(hours=1),
            interval_end=start + timedelta(hours=2),
            pool_price_cad_per_mwh=50.0,
        ),
    ]
    history.get_historical_pool_prices.return_value = _pool_resp(prices)

    gen_intervals = [
        SimpleNamespace(
            interval_start_utc=start,
            asset_id="WND1",
            fuel_type="WIND",
            generation_mw=10.0,
        ),
        SimpleNamespace(
            interval_start_utc=start + timedelta(hours=1),
            asset_id="WND1",
            fuel_type="WIND",
            generation_mw=20.0,
        ),
    ]
    history.get_historical_generation.return_value = SimpleNamespace(
        intervals=gen_intervals,
        warnings=[],
    )

    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, history),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )

    req = CapturePriceRequest(
        start=start,
        end=end,
        asset_ids=["WND1"],
        fuel_types=["WIND"],
    )
    resp = await service.calculate_capture_prices(req)
    assert len(resp.results) == 2
    asset_res = next(r for r in resp.results if r.group == "asset:WND1")
    assert asset_res.generation_mwh == 30.0
    # Capture price: (10 * 100 + 20 * 50) / 30 = 2000 / 30 = 66.666...
    assert round(asset_res.capture_price_cad_per_mwh or 0, 1) == 66.7
    assert asset_res.market_average_price_cad_per_mwh == 75.0


@pytest.mark.asyncio
async def test_analyze_net_load_calculates_renewables_and_differences() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    history = AsyncMock()

    loads = SimpleNamespace(
        intervals=[
            SimpleNamespace(interval_start=start, load_mw=10000.0),
            SimpleNamespace(interval_start=start + timedelta(hours=1), load_mw=11000.0),
        ],
        warnings=[],
    )
    history.get_historical_load.return_value = loads

    generations = SimpleNamespace(
        intervals=[
            SimpleNamespace(interval_start_utc=start, generation_mw=2000.0),
            SimpleNamespace(interval_start_utc=start + timedelta(hours=1), generation_mw=1500.0),
        ],
        warnings=[],
    )
    history.get_historical_generation.return_value = generations

    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, history),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )

    req = NetLoadRequest(start=start, end=end, renewable_fuels=["WIND", "SOLAR"])
    resp = await service.analyze_net_load(req)
    assert len(resp.intervals) == 2
    assert resp.intervals[0].net_load_mw == 8000.0
    assert resp.intervals[1].net_load_mw == 9500.0
    assert resp.average_net_load_mw == 8750.0
    assert resp.peak_net_load_mw == 9500.0
    assert resp.minimum_net_load_mw == 8000.0


@pytest.mark.asyncio
async def test_analyze_supply_stack_filters_hour_and_calculates_marginal() -> None:
    operations = AsyncMock()
    start = datetime(2026, 1, 1, 13, tzinfo=UTC)
    blocks = [
        EnergyMeritOrderBlock(
            interval_start=start,
            asset_id="G1",
            block_price_cad_per_mwh=25.0,
            block_size_mw=50.0,
            to_mw=None,
            from_mw=None,
            dispatched_mw=50.0,
        ),
        EnergyMeritOrderBlock(
            interval_start=start,
            asset_id="G2",
            block_price_cad_per_mwh=75.0,
            block_size_mw=100.0,
            to_mw=None,
            from_mw=None,
            dispatched_mw=20.0,
        ),
        EnergyMeritOrderBlock(
            interval_start=start,
            asset_id="G3",
            block_price_cad_per_mwh=150.0,
            block_size_mw=100.0,
            to_mw=None,
            from_mw=None,
            dispatched_mw=0.0,
        ),
    ]
    operations.get_energy_merit_order.return_value = SimpleNamespace(
        blocks=blocks,
        warnings=[],
    )

    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, SimpleNamespace()),
        cast(OperationsService, operations),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )

    req = SupplyStackRequest(
        report_date=date(2026, 1, 1),
        hour_ending=start.astimezone(MARKET_TZ).hour + 1,
        limit=50,
    )
    resp = await service.analyze_supply_stack(req)
    assert resp.offered_mw == 250.0
    assert resp.dispatched_mw == 70.0
    assert resp.marginal_offer_cad_per_mwh == 75.0


@pytest.mark.asyncio
async def test_analyze_intertie_utilization_ratios_and_constraints() -> None:
    operations = AsyncMock()
    intervals = [
        SimpleNamespace(
            intertie="BC",
            direction="import",
            available_transfer_capability_mw=500.0,
            gross_offer_mw=480.0,  # 0.96 ratio (>= 0.95 -> constrained)
        ),
        SimpleNamespace(
            intertie="BC",
            direction="import",
            available_transfer_capability_mw=500.0,
            gross_offer_mw=250.0,  # 0.50 ratio
        ),
    ]
    operations.get_intertie_capability.return_value = SimpleNamespace(
        intervals=intervals,
        warnings=[],
    )

    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, SimpleNamespace()),
        cast(OperationsService, operations),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )

    req = IntertieUtilizationRequest(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        interties=["BC"],
    )
    resp = await service.analyze_intertie_utilization(req)
    assert len(resp.results) == 1
    res = resp.results[0]
    assert res.intertie == "BC"
    assert res.constrained_hours == 1
    assert res.average_offer_to_capability_ratio == 0.73


@pytest.mark.asyncio
async def test_analyze_generation_mix_aggregates_shares_and_peaks() -> None:
    history = AsyncMock()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    gen_intervals = [
        SimpleNamespace(fuel_type="GAS", generation_mw=6000.0, interval_start_utc=start),
        SimpleNamespace(fuel_type="WIND", generation_mw=2000.0, interval_start_utc=start),
    ]
    history.get_historical_generation.return_value = SimpleNamespace(
        intervals=gen_intervals,
        warnings=[],
    )

    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, history),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )

    req = GenerationAnalysisRequest(start=start, end=end, fuel_types=["GAS", "WIND"])
    resp = await service.analyze_generation_mix(req)
    assert resp.total_generation_mwh == 8000.0
    gas_res = next(r for r in resp.results if r.fuel_type == "GAS")
    assert gas_res.share == 0.75


@pytest.mark.asyncio
async def test_analyze_asset_dispatch_ramp_and_factor() -> None:
    history = AsyncMock()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    gen_intervals = [
        SimpleNamespace(
            interval_start=start,
            interval_start_utc=start,
            asset_id="G1",
            fuel_type="GAS",
            generation_mw=100.0,
            maximum_capability_mw=200.0,
        ),
        SimpleNamespace(
            interval_start=start + timedelta(hours=1),
            interval_start_utc=start + timedelta(hours=1),
            asset_id="G1",
            fuel_type="GAS",
            generation_mw=150.0,
            maximum_capability_mw=200.0,
        ),
    ]
    history.get_historical_generation.return_value = SimpleNamespace(
        intervals=gen_intervals,
        warnings=[],
    )

    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, history),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )

    req = AssetDispatchRequest(start=start, end=end, asset_ids=["G1"])
    resp = await service.analyze_asset_dispatch(req)
    assert len(resp.results) == 1
    res = resp.results[0]
    assert res.asset_id == "G1"
    assert res.generation_mwh == 250.0
    assert res.peak_generation_mw == 150.0
    assert res.average_capacity_factor == 0.625


@pytest.mark.asyncio
async def test_analyze_outage_impact_threshold_and_correlation() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    history = AsyncMock()
    grid = AsyncMock()

    prices = [
        PoolPriceInterval(
            interval_start=start,
            interval_end=start + timedelta(hours=1),
            pool_price_cad_per_mwh=40.0,
        ),
        PoolPriceInterval(
            interval_start=start + timedelta(hours=1),
            interval_end=start + timedelta(hours=2),
            pool_price_cad_per_mwh=140.0,
        ),
    ]
    history.get_historical_pool_prices.return_value = _pool_resp(prices)

    grid.get_outages.return_value = SimpleNamespace(
        outages=[
            SimpleNamespace(interval_start=start, total_outage_mw=500.0),
            SimpleNamespace(interval_start=start + timedelta(hours=1), total_outage_mw=1500.0),
        ],
        warnings=[],
    )

    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, history),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, grid),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )

    req = OutageImpactRequest(start=start, end=end, high_outage_threshold_mw=1000.0)
    resp = await service.analyze_outage_impact(req)
    assert resp.matched_observations == 2
    assert resp.high_outage_observations == 1
    assert resp.average_price_high_outage_cad_per_mwh == 140.0
    assert resp.average_price_other_hours_cad_per_mwh == 40.0
    assert resp.price_difference_cad_per_mwh == 100.0
    assert resp.outage_price_correlation == 1.0


@pytest.mark.asyncio
async def test_analyze_forecast_error_metrics() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    history = AsyncMock()

    history.get_forecast.return_value = SimpleNamespace(
        intervals=[
            SimpleNamespace(
                interval_start=start,
                actual_value=100.0,
                forecast_value=110.0,  # error = +10
                lead_time_minutes=60,
            ),
            SimpleNamespace(
                interval_start=start + timedelta(hours=1),
                actual_value=100.0,
                forecast_value=90.0,  # error = -10
                lead_time_minutes=60,
            ),
        ],
        warnings=[],
    )

    service = ResearchService(
        cast(MarketService, SimpleNamespace()),
        cast(HistoryService, history),
        cast(OperationsService, SimpleNamespace()),
        cast(GridService, SimpleNamespace()),
        cast(OperatingReserveService, SimpleNamespace()),
        Settings(aeso_api_key=SecretStr("test-key")),
    )

    req = ForecastErrorRequest(start=start, end=end, series="ail")
    resp = await service.analyze_forecast_error(req)
    assert resp.observation_count == 2
    assert resp.mean_error_mw == 0.0
    assert resp.mean_absolute_error_mw == 10.0
    assert resp.root_mean_squared_error_mw == 10.0
    assert resp.mean_absolute_percentage_error == 0.1


async def test_generation_mix_aggregates_assets_per_utc_hour() -> None:
    start = datetime(2026, 11, 1, 7, tzinfo=UTC)
    history = AsyncMock()
    # Both hours display as 01:00 in Edmonton. GAS totals are 100 and 200 MW.
    history.get_historical_generation.return_value = SimpleNamespace(
        intervals=[
            SimpleNamespace(
                fuel_type=fuel,
                generation_mw=value,
                interval_start_utc=start + timedelta(hours=hour),
                interval_start=(start + timedelta(hours=hour)).astimezone(MARKET_TZ),
            )
            for fuel, hour, value in [
                ("GAS", 0, 40),
                ("GAS", 0, 60),
                ("GAS", 1, 80),
                ("GAS", 1, 120),
                ("WIND", 0, 20),
                ("WIND", 1, 80),
            ]
        ],
        warnings=["fixture warning"],
    )
    service = ResearchService(
        AsyncMock(),
        history,
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        Settings(aeso_api_key=SecretStr("test-key")),
    )
    result = await service.analyze_generation_mix(
        GenerationAnalysisRequest(
            start=start,
            end=start + timedelta(hours=2),
        )
    )
    gas = next(item for item in result.results if item.fuel_type == "GAS")
    assert gas.generation_mwh == 300
    assert gas.share == 0.75
    assert gas.average_generation_mw == 150
    assert gas.peak_generation_mw == 200
    assert result.total_generation_mwh == 400
    assert "fixture warning" in result.warnings


@pytest.mark.parametrize("available_hours", [0, 1, 2])
async def test_net_load_excludes_missing_generation_but_retains_observed_zero(
    available_hours,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    history = AsyncMock()
    history.get_historical_load.return_value = SimpleNamespace(
        intervals=[
            SimpleNamespace(interval_start=start + timedelta(hours=hour), load_mw=9000)
            for hour in range(2)
        ],
        warnings=[],
    )
    history.get_historical_generation.return_value = SimpleNamespace(
        intervals=[
            SimpleNamespace(interval_start_utc=start + timedelta(hours=hour), generation_mw=0)
            for hour in range(available_hours)
        ],
        warnings=[],
    )
    service = ResearchService(
        AsyncMock(),
        history,
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        Settings(aeso_api_key=SecretStr("test-key")),
    )
    response = await service.analyze_net_load(
        NetLoadRequest(start=start, end=start + timedelta(hours=2))
    )
    assert len(response.intervals) == available_hours
    assert response.average_net_load_mw == (9000 if available_hours else None)
    assert (
        response.metadata.completeness
        == [DataCompleteness.EMPTY, DataCompleteness.PARTIAL, DataCompleteness.COMPLETE][
            available_hours
        ]
    )
    assert response.metadata.extra["unmatched_load_observations"] == 2 - available_hours
    assert bool(response.warnings) == (available_hours < 2)
