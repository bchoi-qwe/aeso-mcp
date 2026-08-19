#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Smoke-test the built wheel without importing the checkout's source tree."""

from __future__ import annotations

import asyncio
import sys
from importlib.metadata import version as installed_version
from pathlib import Path

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
    "aeso://capabilities",
}


def _assert_imported_from_wheel() -> None:
    """Reject accidental imports from the checkout's source tree."""
    checkout_source = (Path.cwd() / "src" / "aeso_mcp").resolve()
    module_path = Path(aeso_mcp.__file__).resolve()
    if module_path.is_relative_to(checkout_source):
        raise SystemExit(f"Wheel smoke imported checkout source: {module_path}")


async def smoke() -> None:
    """List the packaged MCP surface and read a packaged data resource."""
    _assert_imported_from_wheel()

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
        glossary = await mcp.read_resource("aeso://glossary")
        if "Pool Price" not in glossary.contents[0].content:
            raise SystemExit("Installed wheel glossary resource did not load")
    finally:
        await container.aclose()

    sys.stdout.write(f"Installed wheel MCP smoke passed ({installed_version('aeso-mcp')})\n")


if __name__ == "__main__":
    asyncio.run(smoke())
