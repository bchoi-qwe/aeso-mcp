#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded in-process MCP catalog benchmark; no external market or model calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import sys
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from fastmcp import Client

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server

QUERY = "Find historical hourly electricity price distribution"


async def measure(operation: Callable[[], Awaitable[Any]], samples: int) -> dict[str, float]:
    """Measure warm operations; serialization is performed outside each timed call."""
    await operation()
    elapsed = []
    for _ in range(samples):
        start = perf_counter()
        await operation()
        elapsed.append((perf_counter() - start) * 1000)
    elapsed.sort()
    return {
        "samples": samples,
        "p50_ms": statistics.median(elapsed),
        "p95_ms": elapsed[math.ceil(0.95 * samples) - 1],
        "max_ms": elapsed[-1],
    }


async def benchmark(mode: str, samples: int) -> dict[str, Any]:
    settings = Settings.model_validate({"tool_discovery_mode": mode, "aeso_api_key": None})
    container = build_container(settings)
    try:
        async with Client(create_mcp_server(settings, container)) as client:
            tools = await client.list_tools()
            encoded = json.dumps(
                [tool.model_dump(mode="json", exclude_none=True) for tool in tools],
                separators=(",", ":"),
            ).encode()
            search_name = "search_tools" if mode == "compact" else "search_aeso_tools"

            async def search() -> Any:
                return await client.call_tool(search_name, {"query": QUERY})

            return {
                "tool_count": len(tools),
                "catalog_bytes": len(encoded),
                "list_tools": await measure(client.list_tools, samples),
                "tool_discovery": await measure(search, samples),
            }
    finally:
        await container.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.samples <= 100:
        parser.error("--samples must be between 5 and 100")
    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "scope": "Warm in-process discovery only; no live market or external model calls",
        "full": asyncio.run(benchmark("full", args.samples)),
        "compact": asyncio.run(benchmark("compact", args.samples)),
    }
    if sys.platform.startswith("linux"):
        import resource

        report["process_peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
