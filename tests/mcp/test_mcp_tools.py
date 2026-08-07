# SPDX-License-Identifier: MIT
"""MCP protocol/application boundary tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from aeso_mcp.app import AppContainer
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server
from aeso_mcp.models.common import DatasetMetadata, DataStatus, ProviderName
from aeso_mcp.models.generation import (
    FuelMixComponent,
)
from aeso_mcp.models.grid import (
    InterchangePathFlow,
)
from aeso_mcp.models.prices import PoolPriceInterval
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.services.assets import AssetsService
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.grid import GridService
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import MARKET_TZ, utc_now

EXPECTED_TOOLS = {
    "get_market_snapshot",
    "get_pool_prices",
    "get_system_marginal_prices",
    "get_load",
    "get_generation",
    "get_interchange",
    "get_reserves",
    "get_outages",
    "get_assets",
    "compare_market_periods",
    "find_price_events",
    "explain_market_conditions",
    "compare_forecast_to_actual",
}

EXPECTED_RESOURCES = {
    "aeso://glossary",
    "aeso://datasets",
    "aeso://methodology/pool-price",
    "aeso://methodology/system-marginal-price",
}


def _meta(dataset: str = "test") -> DatasetMetadata:
    return DatasetMetadata(
        dataset=dataset,
        retrieved_at=utc_now(),
        status=DataStatus.ACTUAL,
        provider=ProviderName.DERIVED,
        units={"pool_price_cad_per_mwh": "CAD/MWh"},
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(aeso_api_key=SecretStr("test-key"))


@pytest.fixture
def container(settings: Settings) -> AppContainer:
    provider = AsyncMock()
    cache = AsyncTTLCache()
    market = MarketService(provider, settings, cache)
    grid = GridService(provider, settings, cache)
    assets = AssetsService(provider, settings, cache)
    analytics = AnalyticsService(market, settings)

    # Seed provider responses used by tools
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    provider.get_pool_prices.return_value = (
        [
            PoolPriceInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                pool_price_cad_per_mwh=42.0,
            )
        ],
        {"provider": "gridstatus", "source_product": "Pool Price API", "api_version": "v1.1"},
    )
    provider.get_system_marginal_prices.return_value = (
        [],
        {"provider": "gridstatus", "source_product": "SMP"},
    )
    provider.get_load.return_value = ([], {"provider": "gridstatus", "source_product": "Load"})
    provider.get_fuel_mix.return_value = (
        start,
        [FuelMixComponent(fuel_type="Wind", generation_mw=1000.0)],
        {"provider": "gridstatus", "source_product": "CSD"},
    )
    provider.get_generation_history.return_value = (
        [],
        {"provider": "gridstatus", "source_product": "Wind"},
    )
    provider.get_interchange.return_value = (
        start,
        [InterchangePathFlow(path="British Columbia", flow_mw=100.0)],
        100.0,
        {"provider": "gridstatus", "source_product": "CSD"},
    )
    provider.get_reserves.return_value = (
        start,
        {"contingency_reserve_required_mw": 400.0},
        {"provider": "gridstatus", "source_product": "CSD"},
    )
    provider.get_supply_demand_snapshot.return_value = (
        start,
        {
            "generation_by_fuel": [FuelMixComponent(fuel_type="Wind", generation_mw=1000.0)],
            "total_generation_mw": 1000.0,
            "interchange_paths": [InterchangePathFlow(path="British Columbia", flow_mw=100.0)],
            "net_interchange_mw": 100.0,
            "reserves": {"contingency_reserve_required_mw": 400.0},
            "alberta_internal_load_mw": 9000.0,
        },
        {"provider": "gridstatus", "source_product": "CSD", "api_version": "v2"},
    )
    provider.get_assets.return_value = ([], {"provider": "gridstatus", "source_product": "Assets"})
    provider.get_outages.return_value = (
        [],
        {"provider": "gridstatus", "source_product": "Outages"},
    )

    return AppContainer(
        settings=settings,
        cache=cache,
        market=market,
        grid=grid,
        assets=assets,
        analytics=analytics,
    )


@pytest.mark.asyncio
async def test_tool_discovery_stable(container: AppContainer, settings: Settings) -> None:
    mcp = create_mcp_server(settings, container)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_resource_discovery(container: AppContainer, settings: Settings) -> None:
    mcp = create_mcp_server(settings, container)
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert uris == EXPECTED_RESOURCES


@pytest.mark.asyncio
async def test_pool_price_structured_output(container: AppContainer, settings: Settings) -> None:
    from fastmcp import Client

    mcp = create_mcp_server(settings, container)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_pool_prices",
            {
                "request": {
                    "start": "2024-01-15T00:00:00-07:00",
                    "end": "2024-01-16T00:00:00-07:00",
                }
            },
        )
        assert result.data is not None or result.structured_content is not None
        payload = result.structured_content or result.data
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        assert "intervals" in payload
        assert payload["intervals"][0]["pool_price_cad_per_mwh"] == 42.0
        assert payload["metadata"]["units"]["pool_price_cad_per_mwh"] == "CAD/MWh"


@pytest.mark.asyncio
async def test_invalid_range_rejected(container: AppContainer, settings: Settings) -> None:
    from fastmcp import Client

    mcp = create_mcp_server(settings, container)
    async with Client(mcp) as client:
        with pytest.raises(Exception) as exc:
            await client.call_tool(
                "get_pool_prices",
                {
                    "request": {
                        "start": "2024-01-01T00:00:00-07:00",
                        "end": "2025-12-31T00:00:00-07:00",
                    }
                },
            )
        message = str(exc.value)
        assert "maximum" in message.lower() or "range" in message.lower()


@pytest.mark.asyncio
async def test_glossary_resource(container: AppContainer, settings: Settings) -> None:
    from fastmcp import Client

    mcp = create_mcp_server(settings, container)
    async with Client(mcp) as client:
        content = await client.read_resource("aeso://glossary")
        text = ""
        if isinstance(content, list):
            text = "".join(getattr(part, "text", str(part)) for part in content)
        else:
            text = str(content)
        assert "Pool Price" in text
        assert "America/Edmonton" in text or "AIL" in text


@pytest.mark.asyncio
async def test_snapshot_tool(container: AppContainer, settings: Settings) -> None:
    from fastmcp import Client

    mcp = create_mcp_server(settings, container)
    async with Client(mcp) as client:
        result = await client.call_tool("get_market_snapshot", {})
        payload = result.structured_content or result.data
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        assert payload["alberta_internal_load_mw"] == 9000.0
        assert payload["wind_generation_mw"] == 1000.0
