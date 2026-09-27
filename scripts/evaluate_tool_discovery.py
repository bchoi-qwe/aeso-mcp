#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Measure full-catalog and FastMCP BM25 progressive discovery on canonical eval cases."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.server.transforms.search import BM25SearchTransform
from pydantic import SecretStr

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "tests/evals/cases.json"
ALWAYS_VISIBLE = ("get_market_snapshot", "analyze_market_event", "get_forecast")
MAX_SEARCH_RESULTS = 5


def _serialize_tool_list(tools: list[Any]) -> str:
    """Serialize MCP wire-level tool definitions for reproducible size estimates."""
    return json.dumps(
        [tool.model_dump(mode="json", exclude_none=True) for tool in tools],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _result_text(result: Any) -> str:
    """Collect text content from a FastMCP client tool result."""
    return "".join(
        text for item in result.content if isinstance(text := getattr(item, "text", None), str)
    )


async def evaluate_tool_discovery() -> dict[str, Any]:
    """Run the canonical catalog against full listing and FastMCP's BM25 transform."""
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError(f"Expected an array in {CASES_PATH}")

    settings = Settings(aeso_api_key=SecretStr("tool-discovery-eval-key"))
    full_container = build_container(settings)
    try:
        full_server = create_mcp_server(settings, full_container)
        async with Client(full_server) as client:
            full_tools = await client.list_tools()
    finally:
        await full_container.aclose()

    progressive_container = build_container(settings)
    try:
        progressive_server = create_mcp_server(settings, progressive_container)
        progressive_server.add_transform(
            BM25SearchTransform(
                max_results=MAX_SEARCH_RESULTS,
                always_visible=list(ALWAYS_VISIBLE),
            )
        )
        async with Client(progressive_server) as client:
            visible_tools = await client.list_tools()
            search_result_chars = 0
            supported_search_result_chars = 0
            unsupported_search_result_chars = 0
            supported_case_count = 0
            discovery_calls = 0
            top1_correct = 0
            top5_complete = 0
            top1_misses: list[str] = []
            top5_misses: list[dict[str, Any]] = []
            unsupported_cases = 0
            unsupported_suggestions: list[dict[str, Any]] = []
            schema_retrieval_failures: list[str] = []

            for case in cases:
                case_id = str(case["id"])
                expected = {str(name) for name in case["expected_tools"]}
                if not expected:
                    # Probe unsupported/refusal scenarios separately. Correct callers should not
                    # invoke a data tool, but the ranking's suggestions reveal abstention risk.
                    unsupported_cases += 1
                    result = await client.call_tool(
                        "search_tools", {"query": str(case["question"])}
                    )
                    text = _result_text(result)
                    search_result_chars += len(text)
                    unsupported_search_result_chars += len(text)
                    definitions = json.loads(text)
                    names = [str(tool["name"]) for tool in definitions]
                    if names:
                        unsupported_suggestions.append({"id": case_id, "tools": names})
                    continue

                supported_case_count += 1
                needed = expected.difference(ALWAYS_VISIBLE)
                definitions: list[dict[str, Any]] = []
                if needed:
                    discovery_calls += 1
                    result = await client.call_tool(
                        "search_tools", {"query": str(case["question"])}
                    )
                    text = _result_text(result)
                    search_result_chars += len(text)
                    supported_search_result_chars += len(text)
                    parsed = json.loads(text)
                    if not isinstance(parsed, list):
                        raise ValueError(f"BM25 returned a non-list result for {case_id}")
                    definitions = parsed

                ranked_names = [str(tool["name"]) for tool in definitions]
                discoverable = set(ALWAYS_VISIBLE).union(ranked_names)
                if expected.issubset(discoverable):
                    top5_complete += 1
                else:
                    top5_misses.append(
                        {
                            "id": case_id,
                            "expected": sorted(expected),
                            "returned": ranked_names,
                        }
                    )

                pinned_expected = sorted(expected.intersection(ALWAYS_VISIBLE))
                first_choice = (
                    pinned_expected[0]
                    if pinned_expected
                    else ranked_names[0]
                    if ranked_names
                    else None
                )
                if first_choice in expected:
                    top1_correct += 1
                else:
                    top1_misses.append(case_id)

                for tool_name in needed.intersection(ranked_names):
                    definition = next(tool for tool in definitions if tool["name"] == tool_name)
                    if not isinstance(definition.get("inputSchema"), dict) or not isinstance(
                        definition.get("outputSchema"), dict
                    ):
                        schema_retrieval_failures.append(case_id)
                        break

        full_catalog = _serialize_tool_list(full_tools)
        progressive_catalog = _serialize_tool_list(visible_tools)
        full_catalog_chars = len(full_catalog)
        progressive_catalog_chars = len(progressive_catalog)
        full_case_chars = full_catalog_chars * len(cases)
        progressive_case_chars = progressive_catalog_chars * len(cases) + search_result_chars
        return {
            "method": "FastMCP BM25SearchTransform, max_results=5, three always-visible tools",
            "canonical_cases": len(cases),
            "supported_route_cases": supported_case_count,
            "no_tool_cases": unsupported_cases,
            "full_catalog_tool_count": len(full_tools),
            "progressive_catalog_tool_count": len(visible_tools),
            "always_visible": list(ALWAYS_VISIBLE),
            "discovery_calls_for_supported_routes": discovery_calls,
            "additional_tool_round_trips": discovery_calls,
            "top1_correct_or_pinned": top1_correct,
            "top1_miss_cases": top1_misses,
            "top5_complete_routes": top5_complete,
            "top5_miss_cases": top5_misses,
            "schema_retrieval_failures": schema_retrieval_failures,
            "no_tool_cases_with_search_suggestions": unsupported_suggestions,
            "full_catalog_wire_chars": full_catalog_chars,
            "full_catalog_rough_tokens_at_4_chars_each": full_catalog_chars // 4,
            "progressive_catalog_wire_chars": progressive_catalog_chars,
            "progressive_catalog_rough_tokens_at_4_chars_each": progressive_catalog_chars // 4,
            "search_result_wire_chars_for_supported_routes": supported_search_result_chars,
            "search_result_rough_tokens_for_supported_routes_at_4_chars_each": (
                supported_search_result_chars // 4
            ),
            "search_result_wire_chars_for_no_tool_cases": unsupported_search_result_chars,
            "search_result_wire_chars_for_all_cases": search_result_chars,
            "full_case_catalog_chars": full_case_chars,
            "progressive_case_catalog_and_discovery_chars": progressive_case_chars,
            "estimated_character_reduction_percent": round(
                100 * (1 - progressive_case_chars / full_case_chars), 1
            ),
            "token_estimate_note": (
                "Character counts / 4 are rough comparisons, not tokenizer measurements; excludes "
                "conversation text, tool arguments/results, and client-specific caching."
            ),
            "model_answer_benchmark_run": False,
        }
    finally:
        await progressive_container.aclose()


def main() -> None:
    """Print the benchmark result as JSON."""
    sys.stdout.write(json.dumps(asyncio.run(evaluate_tool_discovery()), indent=2, sort_keys=True))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
