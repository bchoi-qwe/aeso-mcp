#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify every numeric benchmark oracle using actual FastMCP tool responses."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.independent_agent_cases import build_cases, case_digest
from scripts.independent_agent_fixture import execute

SERIES = {
    "get_pool_prices": ("pool_price_cad_per_mwh", "CAD/MWh"),
    "get_load": ("load_mw", "MW"),
}


def score_observations(
    case: dict[str, Any], results: list[dict[str, Any]]
) -> tuple[float, str]:
    """Calculate from returned MCP values, never from the fixture's source arrays."""
    start = datetime.fromisoformat(str(case["start"])).astimezone(UTC)
    end = datetime.fromisoformat(str(case["end"])).astimezone(UTC)
    hours = int((end - start).total_seconds() // 3600)
    expected_grid = [start + timedelta(hours=i) for i in range(hours)]
    values: dict[str, list[float]] = {}
    for result in results:
        name = result["name"]
        if name not in SERIES or name in values:
            raise ValueError("Unexpected or duplicate MCP tool")
        key, unit = SERIES[name]
        if result["units"].get(key) != unit:
            raise ValueError("Unexpected MCP unit")
        rows = result["intervals"]
        if len(rows) != hours:
            raise ValueError("MCP omitted or duplicated an interval")
        observed: dict[datetime, float] = {}
        for row in rows:
            stamp = datetime.fromisoformat(row["interval_start"]).astimezone(UTC)
            if stamp in observed or stamp not in expected_grid:
                raise ValueError("MCP returned duplicate or out-of-range interval")
            value = row[key]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("MCP returned an invalid actual")
            observed[stamp] = float(value)
        if set(observed) != set(expected_grid):
            raise ValueError("MCP interval grid is not complete")
        values[name] = [observed[stamp] for stamp in expected_grid]
    if sorted(values) != sorted(case["expected_tools"]):
        raise ValueError("Expected tool observations missing")
    category = case["category"]
    prices = values.get("get_pool_prices")
    loads = values.get("get_load")
    if category == "price_point":
        assert prices is not None
        return prices[0], "CAD/MWh"
    if category == "load_point":
        assert loads is not None
        return loads[0], "MW"
    if category == "price_mean":
        assert prices is not None
        return sum(prices) / len(prices), "CAD/MWh"
    if category == "price_max":
        assert prices is not None
        return max(prices), "CAD/MWh"
    if category == "price_min":
        assert prices is not None
        return min(prices), "CAD/MWh"
    if category == "load_peak":
        assert loads is not None
        return max(loads), "MW"
    if category == "load_weighted_price":
        assert prices is not None and loads is not None
        if not sum(loads):
            raise ValueError("Undefined load-weighted price")
        return sum(price * load for price, load in zip(prices, loads, strict=True)) / sum(
            loads
        ), "CAD/MWh"
    if category == "price_at_peak_load":
        assert prices is not None and loads is not None
        peak_index = max(range(len(loads)), key=lambda i: (loads[i], -i))
        return prices[peak_index], "CAD/MWh"
    raise ValueError(f"Unexpected category: {category}")


async def verify() -> dict[str, Any]:
    cases = build_cases()
    checked = Counter()
    for case in cases:
        if not case["expected_tools"]:
            checked["unsupported_no_tool_cases"] += 1
            continue
        command = {
            "operation": "call",
            "calls": [
                {
                    "name": name,
                    "arguments": {
                        "request": {"start": case["start"], "end": case["end"]}
                    },
                }
                for name in case["expected_tools"]
            ],
        }
        data = await execute(command)
        value, unit = score_observations(case, data["results"])
        if (
            unit != case["expected_unit"]
            or not math.isclose(value, case["expected_value"], abs_tol=0.025, rel_tol=0)
        ):
            raise ValueError(f"MCP differs from independent arithmetic oracle: {case['id']}")
        checked[str(case["category"])] += 1
    if sum(checked.values()) != 54 or checked["unsupported_no_tool_cases"] != 6:
        raise ValueError("Incomplete reference validation")
    return {
        "scope": "Model-free, in-process FastMCP fixture execution and independent arithmetic",
        "case_sha256": case_digest(cases),
        "total_cases": len(cases),
        "numeric_cases_verified": len(cases) - checked["unsupported_no_tool_cases"],
        "unsupported_not_executed": checked["unsupported_no_tool_cases"],
        "by_category": dict(sorted(checked.items())),
        "actual_live_aeso_values_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(verify())
    content = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")


if __name__ == "__main__":
    main()
