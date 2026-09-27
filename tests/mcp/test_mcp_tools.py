# SPDX-License-Identifier: MIT
"""MCP protocol/application boundary tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.server.transforms.search import BM25SearchTransform
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
from aeso_mcp.models.transmission import TransmissionOutageRecord
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.services.assets import AssetsService
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.grid import GridService
from aeso_mcp.services.market import MarketService
from aeso_mcp.services.operations import OperationsService
from aeso_mcp.services.transmission import TransmissionService
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
    "get_approved_transmission_outages",
    "get_long_range_transmission_outages",
    "get_assets",
    "get_monthly_cumulative_net_revenue",
    "get_secondary_offer_price_limit",
    "compare_market_periods",
    "find_price_events",
    "explain_market_conditions",
    "compare_forecast_to_actual",
    "calculate_asset_energy_revenue",
    "compare_csd_to_metered",
    "analyze_ramps",
    "analyze_supply_surplus_events",
    "get_energy_merit_order",
    "get_unit_commitments",
    "get_generation_capacity",
    "get_load_outage_forecast",
    "get_intertie_capability",
    "get_intertie_outages",
    "get_metered_volumes",
    "get_operating_reserve_offer_control",
    "summarize_market_history",
    "assess_supply_tightness",
    "get_historical_generation",
    "sync_historical_store",
    "get_historical_store_status",
    "get_forecast",
    "get_uc_settlement_summary",
    "get_supply_adequacy",
    "get_supply_surplus",
    "get_ffr_net_schedule",
    "get_dispatch_down_service",
    "get_tmr_reference_price",
    "get_system_events",
    "get_price_statistics",
    "get_price_duration_curve",
    "analyze_market_event",
    "calculate_capture_prices",
    "analyze_net_load",
    "analyze_supply_stack",
    "analyze_intertie_utilization",
    "analyze_generation_mix",
    "analyze_asset_dispatch",
    "analyze_outage_impact",
    "analyze_forecast_error",
    "get_operating_reserve_prices",
    "get_operating_reserve_forecast",
    "get_operating_reserve_activations",
    "summarize_operating_reserve_market",
    "get_research_data",
    "analyze_participant_concentration",
    "analyze_regional_load_generation",
    "analyze_constrained_volume",
    "analyze_scarcity",
    "analyze_system_frequency",
}

EXPECTED_RESOURCES = {
    "aeso://glossary",
    "aeso://datasets",
    "aeso://methodology/pool-price",
    "aeso://methodology/system-marginal-price",
    "aeso://methodology/load",
    "aeso://methodology/generation",
    "aeso://methodology/generator-outages",
    "aeso://methodology/transmission-outages",
    "aeso://methodology/market-power-mitigation",
    "aeso://methodology/energy-merit-order",
    "aeso://methodology/unit-commitments",
    "aeso://methodology/generation-capacity",
    "aeso://methodology/load-outage-forecast",
    "aeso://methodology/intertie-capability",
    "aeso://methodology/intertie-outages",
    "aeso://methodology/metered-volume",
    "aeso://methodology/operating-reserve-offer-control",
    "aeso://methodology/market-history",
    "aeso://methodology/supply-tightness",
    "aeso://methodology/historical-generation",
    "aeso://methodology/research-analytics",
    "aeso://methodology/official-research-data",
    "aeso://methodology/operating-reserve-market",
    "aeso://methodology/uc-settlement",
    "aeso://methodology/official-forecasts",
    "aeso://methodology/supply-adequacy",
    "aeso://methodology/supply-surplus",
    "aeso://methodology/ffr-net-schedule",
    "aeso://methodology/dds-tmr-system-events",
    "aeso://capabilities",
}

EXPECTED_PROMPTS = {
    "daily_market_brief",
    "investigate_price_event",
    "compare_market_days",
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
    operations = OperationsService(provider, market, settings, cache)

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
    pub = start
    public_reports = AsyncMock()
    public_reports.get_approved_transmission_outages.return_value = (
        [
            TransmissionOutageRecord(
                interval_start=start,
                interval_end=start + timedelta(days=1),
                publication_time=pub,
                transmission_owner="ALTALINK",
                element_type="Outage",
                element="TEST LINE",
                scheduled_activity="maintenance",
                comments=None,
                interconnection=None,
                approval_status="approved",
            )
        ],
        pub,
        {
            "provider": "aeso_public_report",
            "source_product": "Approved Transmission Outages (ETS public report)",
        },
    )
    public_reports.get_long_range_transmission_outages.return_value = (
        [
            TransmissionOutageRecord(
                interval_start=start,
                interval_end=start + timedelta(days=30),
                publication_time=pub,
                transmission_owner="ATCO",
                element="LONG RANGE ELEMENT",
                scheduled_activity="forced outage",
                approval_status="tentative",
            )
        ],
        pub,
        {"provider": "aeso_public_report", "source_product": "Long Range"},
    )
    transmission = TransmissionService(
        approved_provider=public_reports,
        long_range_provider=public_reports,
        settings=settings,
        cache=cache,
    )
    public_reports.get_monthly_cumulative_net_revenue.return_value = (
        [],
        start,
        {"provider": "aeso_public_report", "source_product": "MCSINR"},
    )
    public_reports.get_secondary_offer_price_limit.return_value = (
        [],
        start,
        {"provider": "aeso_public_report", "source_product": "SOC"},
    )
    from aeso_mcp.services.market_power import MarketPowerService

    market_power = MarketPowerService(public_reports, settings, cache)

    history = AsyncMock()
    history.get_historical_pool_prices.side_effect = market.get_pool_prices
    history.get_historical_load.side_effect = market.get_load

    return AppContainer(
        settings=settings,
        cache=cache,
        market=market,
        grid=grid,
        assets=assets,
        analytics=analytics,
        transmission=transmission,
        market_power=market_power,
        operations=operations,
        history=history,
        forecasts=AsyncMock(),
        reports=AsyncMock(),
        research=AsyncMock(),
        research_data=AsyncMock(),
        reserves=AsyncMock(),
        apim_http=AsyncMock(),
        public_reports_http=AsyncMock(),
        archive_http=AsyncMock(),
        historical_store=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_tool_discovery_stable(container: AppContainer, settings: Settings) -> None:
    mcp = create_mcp_server(settings, container)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_protocol_negotiates_modern_and_legacy_clients(
    container: AppContainer, settings: Settings
) -> None:
    mcp = create_mcp_server(settings, container)
    for mode, protocol in (("auto", "2026-07-28"), ("legacy", "2025-11-25")):
        async with Client(mcp, mode=mode) as client:
            assert client.protocol_version == protocol
            assert {tool.name for tool in await client.list_tools()} == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_resource_discovery(container: AppContainer, settings: Settings) -> None:
    mcp = create_mcp_server(settings, container)
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert uris == EXPECTED_RESOURCES


@pytest.mark.asyncio
async def test_prompt_discovery_and_rendering(container: AppContainer, settings: Settings) -> None:
    mcp = create_mcp_server(settings, container)
    prompts = await mcp.list_prompts()
    assert {p.name for p in prompts} == EXPECTED_PROMPTS

    prompt = await mcp.get_prompt("investigate_price_event")
    assert prompt is not None
    assert {argument.name for argument in prompt.arguments or []} == {
        "market_date",
        "threshold_cad_per_mwh",
        "minimum_duration_hours",
    }
    rendered = await prompt.render(
        {
            "market_date": "2024-01-15",
            "threshold_cad_per_mwh": 250.0,
            "minimum_duration_hours": 2.0,
        }
    )
    assert rendered.messages[0].content.text.find("250.0") >= 0
    assert "find_price_events" in rendered.messages[0].content.text


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
        assert "GLOSSARY_MARKDOWN" not in text
        assert "from __future__ import annotations" not in text


@pytest.mark.asyncio
async def test_methodology_and_capabilities_resources(
    container: AppContainer,
    settings: Settings,
) -> None:
    mcp = create_mcp_server(settings, container)
    load = await mcp.read_resource("aeso://methodology/load")
    outages = await mcp.read_resource("aeso://methodology/generator-outages")
    capabilities = await mcp.read_resource("aeso://capabilities")

    assert "Alberta Internal Load" in load.contents[0].content
    assert "total_outage_mw" in outages.contents[0].content
    assert "daily_market_brief" in capabilities.contents[0].content


@pytest.mark.asyncio
async def test_progressive_search_returns_schema_and_proxy_preserves_structured_result(
    container: AppContainer, settings: Settings
) -> None:
    mcp = create_mcp_server(settings, container)
    mcp.add_transform(
        BM25SearchTransform(
            max_results=5,
            always_visible=["get_market_snapshot", "analyze_market_event", "get_forecast"],
        )
    )

    async with Client(mcp) as client:
        visible = await client.list_tools()
        assert {tool.name for tool in visible} == {
            "get_market_snapshot",
            "analyze_market_event",
            "get_forecast",
            "search_tools",
            "call_tool",
        }

        discovered = await client.call_tool(
            "search_tools", {"query": "hourly Alberta Pool Price observations in CAD/MWh"}
        )
        definitions = json.loads(discovered.content[0].text)
        pool_prices = next(tool for tool in definitions if tool["name"] == "get_pool_prices")
        assert pool_prices["inputSchema"]["properties"]["request"]["required"] == ["start", "end"]
        assert pool_prices["outputSchema"]["properties"]["intervals"]

        result = await client.call_tool(
            "call_tool",
            {
                "name": "get_pool_prices",
                "arguments": {
                    "request": {
                        "start": "2024-01-15T00:00:00-07:00",
                        "end": "2024-01-16T00:00:00-07:00",
                    }
                },
            },
        )
        assert not result.is_error
        payload = result.structured_content or result.data
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        assert payload["intervals"][0]["pool_price_cad_per_mwh"] == 42.0
        assert payload["metadata"]["units"]["pool_price_cad_per_mwh"] == "CAD/MWh"


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
