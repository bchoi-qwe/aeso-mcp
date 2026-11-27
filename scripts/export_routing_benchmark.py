#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Freeze actual MCP tool descriptions and BM25 candidates for a routing-only experiment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from fastmcp import Client

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server

PINNED = {"get_market_snapshot", "analyze_market_event", "get_forecast"}


async def export(cases: list[dict[str, Any]]) -> dict[str, Any]:
    full_settings = Settings(tool_discovery_mode="full")
    full_container = build_container(full_settings)
    try:
        async with Client(create_mcp_server(full_settings, full_container)) as client:
            listed = await client.list_tools()
            descriptions = {tool.name: tool.description or "" for tool in listed}
    finally:
        await full_container.aclose()
    compact_settings = Settings(tool_discovery_mode="compact")
    compact_container = build_container(compact_settings)
    try:
        async with Client(create_mcp_server(compact_settings, compact_container)) as client:
            for case in cases:
                result = await client.call_tool("search_tools", {"query": case["question"]})
                payload = "".join(item.text for item in result.content if hasattr(item, "text"))
                discovered = {str(tool["name"]) for tool in json.loads(payload)}
                names = PINNED | discovered
                case["compact_candidates"] = sorted(names & descriptions.keys())
                if not set(case["expected_tools"]) <= descriptions.keys():
                    raise ValueError(f"Unknown expected tool in {case['id']}")
    finally:
        await compact_container.aclose()
    return {"cases": cases, "descriptions": descriptions}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("tests/evals/holdout_routing.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = args.cases.read_bytes()
    result = asyncio.run(export(json.loads(payload)))
    result["case_sha256"] = hashlib.sha256(payload).hexdigest()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
