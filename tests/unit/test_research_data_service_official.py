# SPDX-License-Identifier: MIT
"""Deterministic service tests for official research-data joins and bounds."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.errors import UnsupportedDatasetError
from aeso_mcp.models.assets import AssetRecord, AssetsResponse
from aeso_mcp.models.common import DatasetMetadata, ProviderName
from aeso_mcp.models.operations import EnergyMeritOrderBlock, PageInfo
from aeso_mcp.models.prices import PoolPriceResponse
from aeso_mcp.models.research_data import (
    CongestionAnalysisRequest,
    ConstrainedVolumeInterval,
    FrequencyAnalysisRequest,
    ParticipantConcentrationRequest,
    PlanningAreaLoadGenerationInterval,
    PoolParticipantRecord,
    RegionalAnalysisRequest,
    ResearchDataRequest,
    SystemFrequencyInterval,
)
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.research_data import ResearchDataService
from aeso_mcp.timeutil import MARKET_TZ, add_elapsed, utc_now


def _metadata(dataset: str) -> DatasetMetadata:
    return DatasetMetadata(
        dataset=dataset,
        retrieved_at=utc_now(),
        provider=ProviderName.AESO_PUBLIC_REPORT,
    )


class _FakeResearchProvider:
    def __init__(self, rows: dict[str, list[Any]]) -> None:
        self.rows = rows
        self.calls: list[ResearchDataRequest] = []

    async def get_research_data(
        self, request: ResearchDataRequest
    ) -> tuple[list[Any], dict[str, object]]:
        self.calls.append(request)
        return self.rows.get(request.dataset, []), {
            "provider": "aeso_public_report",
            "source_product": request.dataset,
            "source_publication_date": "2026-01-01",
        }


def _service(
    provider: _FakeResearchProvider,
    *,
    market: Any | None = None,
    operations: Any | None = None,
    assets: Any | None = None,
) -> ResearchDataService:
    settings = Settings.model_validate({"aeso_api_key": "test-key"})
    return ResearchDataService(
        cast(Any, provider),
        settings,
        AsyncTTLCache(),
        market=market,
        operations=operations,
        assets=assets,
    )


@pytest.mark.asyncio
async def test_research_data_service_paginates_and_hides_raw_frequency() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    rows = [
        PlanningAreaLoadGenerationInterval(
            interval_start=start + timedelta(hours=index),
            interval_end=start + timedelta(hours=index + 1),
            region="Central",
            planning_area=str(index),
            load_mw=100,
        )
        for index in range(2)
    ]
    provider = _FakeResearchProvider({"planning_area": rows})
    service = _service(provider)

    response = await service.get_research_data(
        ResearchDataRequest(
            dataset="planning_area",
            start=start,
            end=start + timedelta(hours=2),
            offset=1,
            limit=1,
        )
    )

    assert response.page.total == 2
    assert response.page.next_offset is None
    assert cast(PlanningAreaLoadGenerationInterval, response.records[0]).planning_area == "1"
    assert response.metadata.request_start == start
    with pytest.raises(UnsupportedDatasetError, match="Raw system-frequency"):
        await service.get_research_data(
            ResearchDataRequest(
                dataset="system_frequency",
                start=start,
                end=start + timedelta(minutes=1),
            )
        )


@pytest.mark.asyncio
async def test_regional_analysis_keeps_null_source_fields_out_of_averages() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    rows = [
        PlanningAreaLoadGenerationInterval(
            interval_start=start,
            interval_end=add_elapsed(start, timedelta(hours=1)),
            region="Central",
            planning_area="56",
            load_mw=10,
            system_generation_mw=None,
            actual_load_mw=10,
        ),
        PlanningAreaLoadGenerationInterval(
            interval_start=add_elapsed(start, timedelta(hours=1)),
            interval_end=add_elapsed(start, timedelta(hours=2)),
            region="Central",
            planning_area="56",
            load_mw=20,
            system_generation_mw=10,
            actual_load_mw=20,
        ),
    ]
    result = await _service(_FakeResearchProvider({"planning_area": rows})).analyze_regional(
        RegionalAnalysisRequest(start=start, end=start + timedelta(hours=2))
    )

    summary = result.results[0]
    assert summary.observation_count == 2
    assert summary.average_load_mw == 15
    assert summary.average_system_generation_mw == 10
    assert summary.total_actual_load_mwh == 30
    assert result.metadata.observation_type.value == "derived"


@pytest.mark.asyncio
async def test_frequency_analysis_reports_flagged_interval_exposure_proxy() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    rows = [
        SystemFrequencyInterval(
            interval_start=start,
            interval_end=add_elapsed(start, timedelta(seconds=10)),
            average_frequency_hz=60.0,
            maximum_frequency_hz=60.06,
            minimum_frequency_hz=59.94,
        ),
        SystemFrequencyInterval(
            interval_start=add_elapsed(start, timedelta(seconds=10)),
            interval_end=add_elapsed(start, timedelta(seconds=20)),
            average_frequency_hz=60.01,
            maximum_frequency_hz=60.02,
            minimum_frequency_hz=60.0,
        ),
    ]
    result = await _service(
        _FakeResearchProvider({"system_frequency": rows})
    ).analyze_system_frequency(
        FrequencyAnalysisRequest(start=start, end=start + timedelta(seconds=20))
    )

    assert result.observation_count == 2
    assert result.flagged_interval_seconds_below_lower_threshold == 10
    assert result.flagged_interval_seconds_above_upper_threshold == 10
    assert result.observations_below_lower_threshold == 1
    assert result.observations_above_upper_threshold == 1
    assert "proxy" in result.methodology
    assert "upper bound" in result.methodology


@pytest.mark.asyncio
async def test_participant_concentration_reports_unmapped_blocks() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    participant = PoolParticipantRecord(
        pool_participant_id="PP1",
        pool_participant_name="Participant One",
    )
    provider = _FakeResearchProvider({"pool_participants": [participant]})
    assets = AsyncMock()
    assets.get_assets.return_value = AssetsResponse(
        assets=[
            AssetRecord(
                asset_id="GEN1",
                pool_participant_id="PP1",
                pool_participant_name="Participant One",
            )
        ],
        metadata=_metadata("assets"),
    )
    operations = AsyncMock()
    operations.get_energy_merit_order.return_value = type(
        "MeritResponse",
        (),
        {
            "blocks": [
                EnergyMeritOrderBlock(
                    interval_start=start,
                    asset_id="GEN1",
                    block_size_mw=10,
                ),
                EnergyMeritOrderBlock(
                    interval_start=start,
                    asset_id="UNKNOWN",
                    block_size_mw=5,
                ),
            ],
            "metadata": _metadata("merit"),
        },
    )()
    service = _service(provider, operations=operations, assets=assets)

    result = await service.analyze_participant_concentration(
        ParticipantConcentrationRequest(start=start, end=start + timedelta(hours=1))
    )

    assert result.total_offered_volume_mw == 10
    assert result.results[0].pool_participant_id == "PP1"
    assert result.results[0].offered_volume_share == 1
    assert result.matched_block_count == 1
    assert result.unmapped_block_count == 1
    assert result.warnings


@pytest.mark.asyncio
async def test_congestion_analysis_keeps_unmatched_prices_null() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    row = ConstrainedVolumeInterval(
        interval_start=start,
        interval_end=add_elapsed(start, timedelta(hours=1)),
        fuel_type="WIND",
        planning_area="17",
        constrained_volume_mwh=5,
        constrained_minutes=30,
    )
    market = AsyncMock()
    market.get_pool_prices.return_value = PoolPriceResponse(
        intervals=[],
        page=PageInfo(offset=0, limit=0, returned=0, total=0),
        metadata=_metadata("prices"),
    )
    result = await _service(
        _FakeResearchProvider({"constrained_volume": [row]}), market=market
    ).analyze_congestion(CongestionAnalysisRequest(start=start, end=start + timedelta(hours=1)))

    assert result.results[0].constrained_volume_mwh == 5
    assert result.results[0].average_pool_price_cad_per_mwh is None
    assert result.results[0].matched_pool_price_observations == 0
    assert any("matched 0" in warning for warning in result.warnings)
