# SPDX-License-Identifier: MIT
"""Tests for load forecast vs actual analytics."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.config import Settings
from aeso_mcp.models.analytics import CompareForecastToActualRequest
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import MARKET_TZ


@pytest.mark.asyncio
async def test_compare_forecast_to_actual_metrics() -> None:
    provider = AsyncMock()
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    provider.get_load.return_value = (
        [
            {
                "interval_start": start,
                "interval_end": start + timedelta(hours=1),
                "load_mw": 100.0,
                "load_forecast_mw": 90.0,
            },
            {
                "interval_start": start + timedelta(hours=1),
                "interval_end": start + timedelta(hours=2),
                "load_mw": 110.0,
                "load_forecast_mw": 120.0,
            },
        ],
        {"provider": "gridstatus", "source_product": "Load"},
    )
    settings = Settings(aeso_api_key="test-key")  # type: ignore[arg-type]
    analytics = AnalyticsService(MarketService(provider, settings), settings)
    result = await analytics.compare_forecast_to_actual(
        CompareForecastToActualRequest(
            start=start,
            end=start + timedelta(hours=2),
        )
    )
    assert result.observation_count == 2
    assert result.mean_error_mw == pytest.approx(0.0)
    assert result.mean_abs_error_mw == pytest.approx(10.0)
    assert result.rmse_mw == pytest.approx(10.0)
    assert result.max_abs_error_mw == pytest.approx(10.0)
