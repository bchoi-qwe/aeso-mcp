#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Smoke-test the built wheel without importing the checkout's source tree."""

from __future__ import annotations

import asyncio
import os
import sys
from importlib.metadata import version as installed_version
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic import SecretStr

import aeso_mcp
from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server

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
EXPECTED_PROMPTS = {"daily_market_brief", "investigate_price_event", "compare_market_days"}
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


def _assert_imported_from_wheel() -> None:
    """Reject accidental imports from the checkout's source tree."""
    checkout_source = (Path(__file__).resolve().parents[2] / "src" / "aeso_mcp").resolve()
    module_path = Path(aeso_mcp.__file__).resolve()
    if module_path.is_relative_to(checkout_source):
        raise SystemExit(f"Wheel smoke imported checkout source: {module_path}")


async def _stdio_smoke() -> None:
    """Launch the installed console script and negotiate both protocol eras over stdio."""
    executable = Path(sys.executable).with_name("aeso-mcp")
    if not executable.is_file():
        raise SystemExit(f"Installed wheel console script is missing: {executable}")

    for mode, protocol in (("auto", "2026-07-28"), ("legacy", "2025-11-25")):
        transport = StdioTransport(
            command=str(executable),
            args=["--transport", "stdio"],
            keep_alive=False,
        )
        async with Client(transport, mode=mode) as client:
            tools = {tool.name for tool in await client.list_tools()}
            if client.protocol_version != protocol or tools != EXPECTED_TOOLS:
                raise SystemExit(
                    "Installed wheel stdio mismatch: "
                    f"mode={mode}, protocol={client.protocol_version}, "
                    f"tools={sorted(tools ^ EXPECTED_TOOLS)}"
                )


async def smoke() -> None:
    """List the packaged MCP surface and read a packaged data resource."""
    _assert_imported_from_wheel()

    expected_version = os.environ.get("AESO_MCP_EXPECTED_VERSION")
    actual_version = installed_version("aeso-mcp")
    framework_version = installed_version("fastmcp")
    slim_version = installed_version("fastmcp-slim")
    if (framework_version, slim_version) != ("4.0.10", "4.0.10"):
        raise SystemExit(
            "Installed wheel FastMCP framework mismatch: "
            f"fastmcp={framework_version}, fastmcp-slim={slim_version}"
        )
    if expected_version is not None and actual_version != expected_version:
        raise SystemExit(
            f"Installed wheel version mismatch: expected {expected_version}, got {actual_version}"
        )

    settings = Settings(aeso_api_key=SecretStr("wheel-smoke-key"))
    container = build_container(settings)
    try:
        mcp = create_mcp_server(settings, container)
        tools = {tool.name for tool in await mcp.list_tools()}
        prompts = {prompt.name for prompt in await mcp.list_prompts()}
        resources = {str(resource.uri) for resource in await mcp.list_resources()}
        if tools != EXPECTED_TOOLS:
            raise SystemExit(f"Installed wheel tool mismatch: {sorted(tools ^ EXPECTED_TOOLS)}")
        if prompts != EXPECTED_PROMPTS:
            raise SystemExit(
                f"Installed wheel prompt mismatch: {sorted(prompts ^ EXPECTED_PROMPTS)}"
            )
        if resources != EXPECTED_RESOURCES:
            raise SystemExit(
                f"Installed wheel resource mismatch: {sorted(resources ^ EXPECTED_RESOURCES)}"
            )
        async with Client(mcp) as client:
            status = await client.call_tool("get_historical_store_status", {})
            if status.is_error:
                raise SystemExit("Installed package store-status tool failed")
            invalid = await client.call_tool(
                "get_forecast",
                {
                    "request": {
                        "start": "2025-01-01T00:00:00Z",
                        "end": "2025-01-01T01:00:00Z",
                        "as_of": "2025-01-01T00:00:00",
                    }
                },
                raise_on_error=False,
            )
            if not invalid.is_error:
                raise SystemExit("Installed package accepted naive as_of")
        glossary = await mcp.read_resource("aeso://glossary")
        glossary_content = glossary.contents[0].content
        if not isinstance(glossary_content, str) or "Pool Price" not in glossary_content:
            raise SystemExit("Installed wheel glossary resource did not load")
    finally:
        await container.aclose()

    await _stdio_smoke()
    sys.stdout.write(f"Installed wheel MCP and stdio smoke passed ({actual_version})\n")


if __name__ == "__main__":
    asyncio.run(smoke())
