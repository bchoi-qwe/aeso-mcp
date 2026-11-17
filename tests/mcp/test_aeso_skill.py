# SPDX-License-Identifier: MIT
"""Market-research guidance is discoverable as MCP resources and skill manifests."""

from __future__ import annotations

import pytest
from fastmcp import Client

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server


@pytest.mark.asyncio
async def test_market_research_skill_discoverable_without_aeso_credentials() -> None:
    settings = Settings(aeso_api_key=None)
    container = build_container(settings)
    try:
        mcp = create_mcp_server(settings, container)
        async with Client(mcp) as client:
            resources = await client.list_skill_resources()
            assert any(
                str(resource.uri) == "skill://alberta-market-research/SKILL.md"
                for resource in resources
            )
            skill = await client.read_skill("skill://alberta-market-research/SKILL.md")
            assert skill.name == "alberta-market-research"
            assert "America/Edmonton" in skill.instructions
            assert "look-ahead leakage" in skill.instructions
            assert "CAD/MWh" in skill.instructions
    finally:
        await container.aclose()
