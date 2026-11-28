# SPDX-License-Identifier: MIT
"""Actual MCP calls backed solely by a separate 48-hour synthetic data array."""

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
from scripts.independent_agent_cases import HOURS, LOADS, PRICES, START

ALLOWED = frozenset({"get_pool_prices", "get_load"})


def validate_call(name: str, arguments: dict[str, Any]) -> None:
    if name not in ALLOWED or set(arguments) != {"request"}:
        raise ValueError("Unsupported benchmark tool or argument shape")
    request_type = PoolPriceRequest if name == "get_pool_prices" else LoadRequest
    request = request_type.model_validate(arguments["request"])
    start, end = to_utc(request.start), to_utc(request.end)
    if (
        not START <= start < end <= START + timedelta(hours=HOURS)
        or start.minute or start.second or start.microsecond
        or end.minute or end.second or end.microsecond
        or end - start > timedelta(hours=6)
        or request.include_forecast
        or request.offset != 0
        or request.limit < (end - start).total_seconds() / 3600
    ):
        raise ValueError("Benchmark request exceeds the read-only fixture range")


async def execute(command: dict[str, Any]) -> dict[str, Any]:
    settings = Settings(aeso_api_key=None, tool_discovery_mode="full")
    container = build_container(settings)
    provider = AsyncMock()

    async def prices(start: datetime, end: datetime) -> tuple[list[Any], dict[str, str]]:
        rows = [
            PoolPriceInterval(
                interval_start=START + timedelta(hours=i),
                interval_end=START + timedelta(hours=i + 1),
                pool_price_cad_per_mwh=price,
            )
            for i, price in enumerate(PRICES)
            if to_utc(start) <= START + timedelta(hours=i) < to_utc(end)
        ]
        return rows, {"provider": "gridstatus", "source_product": "Synthetic independent oracle"}

    async def load(
        start: datetime, end: datetime, *, include_forecast: bool = False
    ) -> tuple[list[Any], dict[str, str]]:
        if include_forecast:
            raise ValueError("No synthetic forecast series")
        rows = [
            {
                "interval_start": START + timedelta(hours=i),
                "interval_end": START + timedelta(hours=i + 1),
                "load_mw": value,
            }
            for i, value in enumerate(LOADS)
            if to_utc(start) <= START + timedelta(hours=i) < to_utc(end)
        ]
        return rows, {"provider": "gridstatus", "source_product": "Synthetic independent oracle"}

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
            if set(command) != {"operation", "calls"} or command["operation"] != "call":
                raise ValueError("Unrecognized benchmark operation")
            calls = command["calls"]
            if not isinstance(calls, list) or len(calls) not in {1, 2}:
                raise ValueError("Benchmark allows only one or two tool calls")
            names = []
            for call in calls:
                if not isinstance(call, dict) or set(call) != {"name", "arguments"}:
                    raise ValueError("Invalid tool call")
                if not isinstance(call["name"], str) or not isinstance(call["arguments"], dict):
                    raise ValueError("Invalid call values")
                validate_call(call["name"], call["arguments"])
                names.append(call["name"])
            if len(names) != len(set(names)):
                raise ValueError("Duplicate benchmark tool invocation")
            results = []
            for call in calls:
                response = await client.call_tool(call["name"], call["arguments"])
                if response.is_error:
                    raise ValueError("Synthetic MCP tool returned an error")
                payload = response.structured_content or response.data
                if hasattr(payload, "model_dump"):
                    payload = payload.model_dump(mode="json")
                if not isinstance(payload, dict):
                    raise ValueError("Missing structured MCP output")
                results.append(
                    {
                        "name": call["name"],
                        "intervals": payload["intervals"],
                        "units": payload["metadata"]["units"],
                    }
                )
            return {"results": results}
    finally:
        await container.aclose()


def main() -> None:
    command = json.loads(sys.stdin.read(65536))
    sys.stdout.write(json.dumps(asyncio.run(execute(command)), default=str) + "\n")


if __name__ == "__main__":
    main()
