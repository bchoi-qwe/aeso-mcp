# SPDX-License-Identifier: MIT
"""Verify opt-in progressive discovery without querying external market sources."""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server


@pytest.mark.asyncio
async def test_compact_mode_searches_full_catalog_without_upstream_calls() -> None:
    settings = Settings(aeso_api_key=None, tool_discovery_mode="compact")
    container = build_container(settings)
    try:
        mcp = create_mcp_server(settings, container)
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}
            assert names == {
                "get_market_snapshot",
                "analyze_market_event",
                "get_forecast",
                "search_tools",
                "call_tool",
            }
            result = await client.call_tool(
                "search_tools",
                {"query": "historical Alberta pool prices"},
            )
            text = "".join(
                part.text for part in result.content if isinstance(getattr(part, "text", None), str)
            )
            matches = json.loads(text)
            assert any(item["name"] == "get_pool_prices" for item in matches)
            match = next(item for item in matches if item["name"] == "get_pool_prices")
            assert "inputSchema" in match
            assert "outputSchema" in match
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_full_mode_retains_direct_tool_access() -> None:
    settings = Settings(aeso_api_key=None, tool_discovery_mode="full")
    container = build_container(settings)
    try:
        mcp = create_mcp_server(settings, container)
        async with Client(mcp) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert "get_pool_prices" in names
            assert "search_aeso_tools" in names
            assert "call_tool" not in names
    finally:
        await container.aclose()
