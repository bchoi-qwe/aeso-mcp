# SPDX-License-Identifier: MIT
"""Stdio MCP smoke test using FastMCP in-memory client."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server


@pytest.mark.asyncio
async def test_stdio_compatible_inmemory_client_lists_tools() -> None:
    from fastmcp import Client

    settings = Settings(aeso_api_key=SecretStr("test-key"))
    # Use real container wiring; tools that hit providers are not invoked here.
    # For discovery-only smoke we inject a container built with settings.
    container = build_container(settings)
    mcp = create_mcp_server(settings, container)
    try:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert "get_market_snapshot" in names
            assert "compare_forecast_to_actual" in names
            resources = await client.list_resources()
            uris = {str(r.uri) for r in resources}
            assert "aeso://glossary" in uris
    finally:
        await container.aclose()
