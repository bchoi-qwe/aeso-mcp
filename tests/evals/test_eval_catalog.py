# SPDX-License-Identifier: MIT
"""Structural checks for the canonical agent-use evaluation suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from scripts.evaluate_agent_results import score

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server

CASES_PATH = Path(__file__).with_name("cases.json")


def _cases() -> list[dict[str, object]]:
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


def test_eval_catalog_has_fifty_well_formed_cases() -> None:
    cases = _cases()
    assert len(cases) == 50
    identifiers = [case["id"] for case in cases]
    assert len(identifiers) == len(set(identifiers))
    for case in cases:
        assert isinstance(case["question"], str) and case["question"]
        assert isinstance(case["expected_tools"], list)
        assert isinstance(case["required_concepts"], list) and case["required_concepts"]
        assert isinstance(case["forbidden_claims"], list) and case["forbidden_claims"]
        arguments = case["expected_arguments"]
        assert isinstance(arguments, dict)
        assert isinstance(arguments["required_fields"], list)
        assert isinstance(arguments["constraints"], list)
        assert isinstance(case["expected_datasets"], list) and case["expected_datasets"]
        assert isinstance(case["prohibited_interpretations"], list)
        assert case["prohibited_interpretations"] == case["forbidden_claims"]
        assert isinstance(case["required_caveats"], list) and case["required_caveats"]
        assert isinstance(case["expected_numerical_relationships"], list)


def test_eval_scorer_accepts_complete_structural_oracle() -> None:
    cases = _cases()
    results = []
    for case in cases:
        tools = case["expected_tools"]
        arguments = case["expected_arguments"]
        assert isinstance(tools, list) and isinstance(arguments, dict)
        required_fields = arguments["required_fields"]
        assert isinstance(required_fields, list)
        results.append(
            {
                "id": case["id"],
                "selected_tools": tools,
                "tool_arguments": {
                    str(tool): {str(field): "<scenario value>" for field in required_fields}
                    for tool in tools
                },
                "datasets_used": case["expected_datasets"],
                "caveats": case["required_caveats"],
                "numerical_relationships": case["expected_numerical_relationships"],
                "answer": "",
            }
        )

    summary = score(cases, results)
    assert summary["tool_route_recall"] == 1
    assert summary["argument_field_recall"] == 1
    assert summary["dataset_selection_recall"] == 1
    assert summary["required_caveat_recall"] == 1
    assert summary["numerical_relationship_recall"] == 1


@pytest.mark.asyncio
async def test_eval_tool_routes_exist_on_registered_surface() -> None:
    settings = Settings(aeso_api_key=SecretStr("eval-catalog-key"))
    container = build_container(settings)
    try:
        tools = {item.name for item in await create_mcp_server(settings, container).list_tools()}
    finally:
        await container.aclose()
    expected = {
        tool
        for case in _cases()
        for tool in case["expected_tools"]  # type: ignore[union-attr]
    }
    assert expected <= tools
    roadmap = {
        "get_historical_generation",
        "sync_historical_store",
        "calculate_capture_prices",
        "analyze_market_event",
        "get_operating_reserve_prices",
        "summarize_operating_reserve_market",
    }
    assert roadmap <= expected
