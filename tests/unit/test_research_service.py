# SPDX-License-Identifier: MIT
"""Multivariate research-service behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from aeso_mcp.config import Settings
from aeso_mcp.errors import AuthenticationError, InvalidDateRangeError
from aeso_mcp.models.forecasts import OfficialForecastRequest
from aeso_mcp.models.research import MarketEventRequest
from aeso_mcp.services.grid import GridService
from aeso_mcp.services.history import HistoryService
from aeso_mcp.services.market import MarketService
from aeso_mcp.services.operations import OperationsService
from aeso_mcp.services.research import ResearchService
from aeso_mcp.services.reserves import OperatingReserveService


@pytest.mark.asyncio
async def test_market_event_returns_structured_multivariate_evidence() -> None:
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
    assert response.evidence.supply.renewable_generation_share == pytest.approx(0.18)
    assert response.evidence.merit_order.marginal_dispatched_offer_cad_per_mwh == 125
    assert response.evidence.merit_order.offer_volume_hhi == pytest.approx(0.625)
    assert response.evidence.interties.average_offer_to_capability_ratio == 0.9
    assert response.evidence.interties.average_import_gross_offer_mw == 900
    assert response.evidence.reserves.average_active_volume_mw == 200
    assert response.evidence.reserves.standby_activated_volume_mw == 5
    assert response.evidence.reserves.offer_control_block_count == 1
    assert response.evidence.commitment_count == 1
    assert "do not establish" in response.methodology
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
