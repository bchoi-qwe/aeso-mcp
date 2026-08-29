# SPDX-License-Identifier: MIT
"""Behavioral coverage for operational-report service boundaries."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.models.common import DataStatus, FinalityStatus, ObservationType
from aeso_mcp.models.operations import (
    DailyPageRequest,
    DateRangePageRequest,
    GenerationCapacityInterval,
    IntertieCapabilityInterval,
    IntertieCapabilityRequest,
    IntertieOutageRecord,
    LoadOutageForecastInterval,
    MeteredVolumeInterval,
    MeteredVolumeRequest,
    OperatingReserveOfferBlock,
    UnitCommitmentDirective,
)
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.operations import OperationsService
from aeso_mcp.timeutil import MARKET_TZ


def _service() -> tuple[OperationsService, AsyncMock]:
    provider = AsyncMock()
    service = OperationsService(
        provider,
        AsyncMock(),
        Settings(aeso_api_key="test-key"),  # type: ignore[arg-type]
        AsyncTTLCache(),
    )
    return service, provider


@pytest.mark.asyncio
async def test_operational_report_services_normalize_pages_and_metadata() -> None:
    service, provider = _service()
    report_date = date(2024, 8, 1)
    instant = datetime(2024, 8, 1, 1, tzinfo=MARKET_TZ)
    provenance = {"provider": "aeso_apim", "source_product": "Operational Report"}
    provider.get_unit_commitments.return_value = (
        [
            UnitCommitmentDirective(
                asset_id="GEN1",
                issued_at=instant,
                operation_start=instant + timedelta(hours=1),
            )
        ],
        provenance,
    )
    provider.get_generation_capacity.return_value = (
        [
            GenerationCapacityInterval(
                interval_start=instant,
                fuel_type="GAS",
                sub_fuel_type="COMBINED CYCLE",
                maximum_capability_mw=1000,
                available_capability_mw=900,
                operating_outage_mw=50,
                mothball_outage_mw=50,
            )
        ],
        provenance,
    )
    provider.get_load_outage_forecast.return_value = (
        [LoadOutageForecastInterval(interval_start=instant, load_outage_forecast_mw=125)],
        provenance,
    )
    provider.get_intertie_capability.return_value = (
        [
            IntertieCapabilityInterval(
                intertie="BC",
                interval_start=instant,
                hour_ending=2,
                direction="import",
                available_transfer_capability_mw=500,
            )
        ],
        provenance,
    )
    provider.get_intertie_outages.return_value = (
        [
            IntertieOutageRecord(
                element="BC line",
                affected_interties_or_flowgates=["BC"],
                interval_start=instant,
                interval_end=instant + timedelta(hours=2),
            )
        ],
        provenance,
    )
    provider.get_metered_volumes.return_value = (
        [
            MeteredVolumeInterval(
                pool_participant_id="P1",
                asset_id="GEN1",
                asset_class="GAS",
                interval_start=instant,
                metered_volume_mwh=40,
            )
        ],
        provenance,
    )
    provider.get_operating_reserve_offer_control.return_value = (
        [
            OperatingReserveOfferBlock(
                interval_start=instant,
                commodity="OR",
                product="regulating",
                asset_id="GEN1",
                volume_mw=20,
                active_price_cad_per_mwh=30,
            )
        ],
        provenance,
    )

    commitments = await service.get_unit_commitments(
        DateRangePageRequest(start_date=report_date, end_date=report_date, limit=1)
    )
    capacity = await service.get_generation_capacity(
        DateRangePageRequest(start_date=report_date, end_date=report_date, limit=1)
    )
    load_outage = await service.get_load_outage_forecast(
        DateRangePageRequest(start_date=report_date, end_date=report_date, limit=1)
    )
    capability = await service.get_intertie_capability(
        IntertieCapabilityRequest(
            start_date=report_date,
            end_date=report_date,
            start_hour_ending=1,
            end_hour_ending=24,
            include_versions=True,
            limit=1,
        )
    )
    outages = await service.get_intertie_outages(
        DateRangePageRequest(start_date=report_date, end_date=report_date, limit=1)
    )
    metered = await service.get_metered_volumes(
        MeteredVolumeRequest(
            start_date=report_date,
            end_date=report_date,
            asset_ids=["GEN1"],
            limit=1,
        )
    )
    offers = await service.get_operating_reserve_offer_control(
        DailyPageRequest(report_date=report_date, limit=1)
    )

    assert commitments.directives[0].asset_id == "GEN1"
    assert commitments.metadata.finality == FinalityStatus.PRELIMINARY
    assert capacity.intervals[0].available_capability_mw == 900
    assert capacity.metadata.observation_type == ObservationType.ACTUAL
    assert load_outage.intervals[0].load_outage_forecast_mw == 125
    assert load_outage.metadata.status == DataStatus.FORECAST
    assert capability.intervals[0].available_transfer_capability_mw == 500
    assert capability.metadata.finality == FinalityStatus.PRELIMINARY
    assert outages.outages[0].affected_interties_or_flowgates == ["BC"]
    assert metered.intervals[0].metered_volume_mwh == 40
    assert metered.metadata.completeness.value == "complete"
    assert offers.blocks[0].active_price_cad_per_mwh == 30
    assert offers.metadata.finality == FinalityStatus.FINAL
    provider.get_intertie_capability.assert_awaited_once_with(
        report_date,
        report_date,
        start_hour_ending=1,
        end_hour_ending=24,
        include_versions=True,
    )


@pytest.mark.asyncio
async def test_generation_capacity_marks_future_report_as_forecast() -> None:
    service, provider = _service()
    report_date = datetime.now(tz=MARKET_TZ).date() + timedelta(days=1)
    provider.get_generation_capacity.return_value = ([], {"source_product": "Capacity"})

    response = await service.get_generation_capacity(
        DateRangePageRequest(start_date=report_date, end_date=report_date)
    )

    assert response.intervals == []
    assert response.metadata.status == DataStatus.FORECAST
    assert response.metadata.observation_type == ObservationType.FORECAST
    assert response.metadata.finality == FinalityStatus.PRELIMINARY
