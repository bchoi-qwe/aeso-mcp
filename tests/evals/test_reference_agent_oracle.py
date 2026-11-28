# SPDX-License-Identifier: MIT
"""The independent result calculator must reject malformed MCP observations."""

from datetime import UTC, datetime, timedelta

import pytest
from scripts.independent_agent_cases import build_cases
from scripts.validate_independent_mcp_oracle import score_observations, verify


def test_reference_scorer_rejects_duplicate_or_missing_intervals() -> None:
    case = build_cases()[0]
    row = {
        "interval_start": case["start"],
        "pool_price_cad_per_mwh": 0.0,
    }
    record = {
        "name": "get_pool_prices",
        "units": {"pool_price_cad_per_mwh": "CAD/MWh"},
        "intervals": [row, row],
    }
    with pytest.raises(ValueError, match="omitted or duplicated"):
        score_observations(case, [record])
    record["intervals"] = [row]
    value, unit = score_observations(case, [record])
    assert value == 0.0
    assert unit == "CAD/MWh"
    record["units"] = {"pool_price_cad_per_mwh": "MW"}
    with pytest.raises(ValueError, match="unit"):
        score_observations(case, [record])


def test_reference_scorer_prefers_first_peak_hour() -> None:
    start = datetime(2025, 11, 2, 6, tzinfo=UTC)
    stamps = [(start + timedelta(hours=i)).isoformat() for i in range(3)]
    case = {
        "category": "price_at_peak_load",
        "expected_tools": ["get_pool_prices", "get_load"],
        "start": stamps[0],
        "end": (start + timedelta(hours=3)).isoformat(),
    }
    results = [
        {
            "name": "get_pool_prices",
            "units": {"pool_price_cad_per_mwh": "CAD/MWh"},
            "intervals": [
                {"interval_start": t, "pool_price_cad_per_mwh": p}
                for t, p in zip(stamps, (-10, 0, 50), strict=True)
            ],
        },
        {
            "name": "get_load",
            "units": {"load_mw": "MW"},
            "intervals": [
                {"interval_start": t, "load_mw": mw}
                for t, mw in zip(stamps, (9000, 9000, 7000), strict=True)
            ],
        },
    ]
    assert score_observations(case, results) == (-10, "CAD/MWh")


@pytest.mark.asyncio
async def test_all_reference_cases_execute_through_real_mcp() -> None:
    result = await verify()
    assert result["numeric_cases_verified"] == 48
    assert result["unsupported_not_executed"] == 6
