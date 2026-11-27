# SPDX-License-Identifier: MIT
"""Verify that the model fixture uses the real MCP schemas and known values."""

import pytest
from scripts.benchmark_fixture_agent import CASES, arguments_match, numeric_match, parse_object
from scripts.fixture_agent_tools import execute, validate_call


@pytest.mark.asyncio
async def test_fixture_actual_tools_return_fixed_values() -> None:
    schemas = await execute({"operation": "schemas"})
    assert {tool["name"] for tool in schemas["tools"]} == {"get_load", "get_pool_prices"}
    arguments = {
        "request": {"start": "2025-01-15T08:00:00Z", "end": "2025-01-15T09:00:00Z"}
    }
    result = await execute({"operation": "call", "name": "get_pool_prices", "arguments": arguments})
    assert result["result"]["intervals"][0]["pool_price_cad_per_mwh"] == 0
    assert len(result["result"]["intervals"]) == 1
    assert arguments_match(arguments, CASES[0])
    assert numeric_match({"value": 0, "unit": "CAD/MWh"}, CASES[0])
    assert not numeric_match({"value": False, "unit": "CAD/MWh"}, CASES[0])
    assert parse_object("not JSON") is None


def test_fixture_rejects_other_tools_and_unbounded_ranges() -> None:
    with pytest.raises(ValueError, match="Only two"):
        validate_call("sync_historical_store", {})
    with pytest.raises(ValueError, match="outside"):
        validate_call(
            "get_load",
            {"request": {"start": "2025-01-15T07:00:00Z", "end": "2025-01-16T07:00:00Z"}},
        )
