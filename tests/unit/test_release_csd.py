# SPDX-License-Identifier: MIT
"""Boundary coverage for Current Supply Demand normalization."""

from __future__ import annotations

import pytest

from aeso_mcp.errors import DataValidationError
from aeso_mcp.providers.csd import parse_csd_payload


@pytest.mark.parametrize("payload", [None, {}, {"return": []}])
def test_csd_rejects_invalid_top_level_shapes(payload: object) -> None:
    with pytest.raises(DataValidationError, match="response shape"):
        parse_csd_payload(payload)


def test_csd_rejects_missing_effective_timestamp() -> None:
    with pytest.raises(DataValidationError, match="effective_datetime_utc"):
        parse_csd_payload({"return": {}})


def test_csd_normalizes_optional_values_paths_reserves_and_load_fallback() -> None:
    observed, payload = parse_csd_payload(
        {
            "return": {
                "effective_datetime_utc": "2024-01-15T18:30:00",
                "generation_data_list": [
                    {
                        "fuel_type": "natural_gas",
                        "aggregated_net_generation": "12.5",
                        "aggregated_maximum_capability": "20",
                    },
                    {"fuel_type": "BROKEN", "aggregated_net_generation": "not-number"},
                    "not a mapping",
                ],
                "interchange_list": [
                    {"path": "BC Flow", "actual_flow": "5.5"},
                    {"path": "SK", "actual_flow": ""},
                    {},
                ],
                "contingency_reserve_required": "100",
                "dispatched_contigency_reserve_total": 90,
                "dispatched_contingency_reserve_gen": "80",
                "dispatched_contingency_reserve_other": "10",
                "ffr_armed_dispatch": "2",
                "ffr_offered_volume": "3",
                "long_lead_time_volume": "4",
                "total_ail": "9000",
            }
        }
    )

    assert observed.tzinfo is not None
    assert payload["generation_by_fuel"][0].fuel_type == "Natural Gas"
    assert payload["generation_by_fuel"][0].generation_mw == 12.5
    assert payload["generation_by_fuel"][0].maximum_capability_mw == 20
    assert payload["total_generation_mw"] == 12.5
    assert payload["interchange_paths"][0].path == "BC"
    assert payload["net_interchange_mw"] == 5.5
    assert payload["reserves"] == {
        "contingency_reserve_required_mw": 100.0,
        "dispatched_contingency_reserve_total_mw": 90.0,
        "dispatched_contingency_reserve_gen_mw": 80.0,
        "dispatched_contingency_reserve_other_mw": 10.0,
        "fast_frequency_response_dispatched_mw": 2.0,
        "fast_frequency_response_offered_mw": 3.0,
        "long_lead_time_volume_mw": 4.0,
    }
    assert payload["alberta_internal_load_mw"] == 9000.0


def test_csd_handles_empty_optional_collections_and_missing_load() -> None:
    _, payload = parse_csd_payload(
        {
            "return": {
                "effective_datetime_utc": "2024-01-15T18:30:00Z",
                "generation_data_list": None,
                "interchange_list": None,
                "alberta_internal_load": "",
                "total_ail": None,
            }
        }
    )

    assert payload["generation_by_fuel"] == []
    assert payload["interchange_paths"] == []
    assert payload["net_interchange_mw"] == 0
    assert payload["alberta_internal_load_mw"] is None
