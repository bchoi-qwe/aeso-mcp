# SPDX-License-Identifier: MIT
"""Hourly dispatch ramps must not bridge gaps or collapse DST folds."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from aeso_mcp.config import Settings
from aeso_mcp.models.history import HistoricalGenerationInterval
from aeso_mcp.models.research import AssetDispatchRequest
from aeso_mcp.services.research import ResearchService, _hourly_ramps
from aeso_mcp.timeutil import MARKET_TZ


@pytest.mark.parametrize(
    ("hours", "values", "up", "down"),
    [
        ([0, 1, 3, 4], [10, 20, 200, 195], 10, -5),
        ([0, 2], [10, 200], None, None),
        ([0, 1, 2], [10, 20, 15], 10, -5),
    ],
)
async def test_dispatch_uses_only_consecutive_utc_hours(hours, values, up, down) -> None:
    # 07:00 and 08:00 UTC both display as 01:00 in Edmonton on this date.
    start = datetime(2026, 11, 1, 7, tzinfo=UTC)
    rows = []
    for hour, value in zip(hours, values, strict=True):
        timestamp = start + timedelta(hours=hour)
        rows.append(
            HistoricalGenerationInterval(
                interval_start=timestamp.astimezone(MARKET_TZ),
                interval_end=(timestamp + timedelta(hours=1)).astimezone(MARKET_TZ),
                interval_start_utc=timestamp,
                interval_end_utc=timestamp + timedelta(hours=1),
                resolution="hourly",
                asset_id="G1",
                fuel_type="GAS",
                generation_mw=value,
                source_file_id="fixture",
                source_file_name="fixture.csv",
                source_retrieved_at=start,
            )
        )
    history = AsyncMock()
    history.get_historical_generation.return_value = SimpleNamespace(
        intervals=list(reversed(rows)), warnings=[]
    )
    service = ResearchService(
        AsyncMock(),
        history,
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        Settings(aeso_api_key=SecretStr("test-key")),
    )
    response = await service.analyze_asset_dispatch(
        AssetDispatchRequest(start=start, end=start + timedelta(hours=6), asset_ids=["G1"])
    )
    assert response.results[0].maximum_up_ramp_mw == up
    assert response.results[0].maximum_down_ramp_mw == down
    assert response.results[0].generation_mwh == sum(values)


def test_hourly_ramps_sort_observations_and_exclude_gaps() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert _hourly_ramps(
        {
            start + timedelta(hours=3): 999,
            start + timedelta(hours=1): 20,
            start: 10,
        }
    ) == [10]
