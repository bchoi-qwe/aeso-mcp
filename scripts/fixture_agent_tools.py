# SPDX-License-Identifier: MIT
"""Execute two actual MCP tools against isolated synthetic observations."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock

from fastmcp import Client

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server
from aeso_mcp.models.generation import LoadRequest
from aeso_mcp.models.prices import PoolPriceInterval, PoolPriceRequest
from aeso_mcp.services.history import HistoryService
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import to_utc

START = datetime(2025, 1, 15, 7, tzinfo=UTC)
END = START + timedelta(hours=3)
ALLOWED = {"get_pool_prices", "get_load"}


def validate_call(name: str, arguments: dict[str, Any]) -> None:
    """No arbitrary tool, range, credential, or upstream operation is accepted."""
    if name not in ALLOWED or set(arguments) != {"request"}:
        raise ValueError("Only two fixture tools with a request object are permitted")
    request_type = PoolPriceRequest if name == "get_pool_prices" else LoadRequest
    request = request_type.model_validate(arguments["request"])
    if (
        request.start.utcoffset() is None
        or request.end.utcoffset() is None
        or not START <= to_utc(request.start) < to_utc(request.end) <= END
        or request.include_forecast
        or request.offset != 0
        or request.limit < 3
    ):
        raise ValueError("Request falls outside the bounded actual-only fixture")


async def execute(command: dict[str, Any]) -> dict[str, Any]:
    settings = Settings.model_validate({"aeso_api_key": None, "tool_discovery_mode": "full"})
    container = build_container(settings)
    provider = AsyncMock()
    price_rows = [
        PoolPriceInterval(
            interval_start=START + timedelta(hours=i),
            interval_end=START + timedelta(hours=i + 1),
            pool_price_cad_per_mwh=value,
        )
        for i, value in enumerate((-10.0, 0.0, 40.0))
    ]
    load_rows = [
        {
            "interval_start": START + timedelta(hours=i),
            "interval_end": START + timedelta(hours=i + 1),
            "load_mw": value,
        }
        for i, value in enumerate((8000.0, 8500.0, 9000.0))
    ]

    async def prices(start: datetime, end: datetime) -> tuple[list[Any], dict[str, str]]:
        return (
            [row for row in price_rows if to_utc(start) <= row.interval_start < to_utc(end)],
            {"provider": "gridstatus", "source_product": "Synthetic fixture Pool Price"},
        )

    async def load(
        start: datetime, end: datetime, *, include_forecast: bool = False
    ) -> tuple[list[Any], dict[str, str]]:
        if include_forecast:
            raise ValueError("No forecast fixture")
        return (
            [row for row in load_rows if to_utc(start) <= row["interval_start"] < to_utc(end)],
            {"provider": "gridstatus", "source_product": "Synthetic fixture AIL"},
        )

    provider.get_pool_prices.side_effect = prices
    provider.get_load.side_effect = load
    market = MarketService(provider, settings, container.cache)
    history = AsyncMock()
    history.get_historical_pool_prices.side_effect = market.get_pool_prices
    history.get_historical_load.side_effect = market.get_load
    container.market = market
    container.history = cast(HistoryService, history)
    try:
        async with Client(create_mcp_server(settings, container)) as client:
            if command == {"operation": "schemas"}:
                return {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.inputSchema,
                        }
                        for tool in await client.list_tools()
                        if tool.name in ALLOWED
                    ]
                }
            if set(command) != {"operation", "name", "arguments"} or command["operation"] != "call":
                raise ValueError("Invalid fixture command")
            name = command["name"]
            arguments = command["arguments"]
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ValueError("Invalid tool request")
            validate_call(name, arguments)
            result = await client.call_tool(name, arguments)
            if result.is_error:
                raise ValueError("Fixture MCP tool returned an error")
            payload = result.structured_content or result.data
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump(mode="json")
            if not isinstance(payload, dict):
                raise ValueError("Fixture tool did not return structured data")
            return {"result": payload}
    finally:
        await container.aclose()


def main() -> None:
    command = json.loads(sys.stdin.read(65536))
    result = asyncio.run(execute(command))
    sys.stdout.write(json.dumps(result, default=str) + "\n")


if __name__ == "__main__":
    main()
