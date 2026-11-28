# SPDX-License-Identifier: MIT
"""Cases and scores are independent of the answers returned by the MCP."""

from datetime import UTC, datetime

import pytest
from scripts.benchmark_independent_agent import arguments_match, numeric_match, parse_calls
from scripts.independent_agent_cases import (
    HOURS,
    LOADS,
    PRICES,
    START,
    build_cases,
    shared_model_subset,
)
from scripts.independent_agent_fixture import execute, validate_call


def test_independent_cases_are_stratified_and_cover_dst_and_edge_values() -> None:
    cases = build_cases()
    assert len(cases) == 54
    assert len(shared_model_subset(cases)) == 16
    assert len({case["id"] for case in cases}) == len(cases)
    assert datetime(2025, 11, 2, 6, tzinfo=UTC) == START
    assert len(PRICES) == len(LOADS) == HOURS == 48
    assert min(PRICES) < 0
    assert 0 in PRICES
    assert max(PRICES) >= 450
    assert sum(case["category"] == "unsupported" for case in cases) == 6


@pytest.mark.asyncio
async def test_independent_fixture_uses_actual_mcp_tools() -> None:
    schemas = await execute({"operation": "schemas"})
    assert {tool["name"] for tool in schemas["tools"]} == {"get_pool_prices", "get_load"}
    case = next(case for case in build_cases() if case["category"] == "load_weighted_price")
    calls = [
        {
            "name": tool,
            "arguments": {"request": {"start": case["start"], "end": case["end"]}},
        }
        for tool in case["expected_tools"]
    ]
    assert arguments_match(calls, case)
    result = await execute({"operation": "call", "calls": calls})
    assert len(result["results"]) == 2
    assert len(result["results"][0]["intervals"]) == 4
    assert len(result["results"][1]["intervals"]) == 4
    assert numeric_match({"value": case["expected_value"], "unit": "CAD/MWh"}, case)


def test_invalid_output_and_unbounded_calls_are_rejected() -> None:
    assert parse_calls({"calls": [{"name": "sync_historical_store", "arguments": {}}]}) is None
    assert parse_calls({"calls": [{"name": "get_load", "arguments": {}}] * 2}) is None
    with pytest.raises(ValueError, match="Unsupported"):
        validate_call("submit_energy_offer", {})
    with pytest.raises(ValueError, match="read-only"):
        validate_call(
            "get_pool_prices",
            {"request": {"start": START.isoformat(), "end": "2030-01-01T00:00:00Z"}},
        )
    numeric = next(case for case in build_cases() if case["category"] == "price_point")
    assert not numeric_match({"value": False, "unit": "CAD/MWh"}, numeric)
    assert not numeric_match({"value": 0, "unit": "MW"}, numeric)
