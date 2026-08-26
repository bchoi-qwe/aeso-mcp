#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Score externally produced agent answers against the canonical eval catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON list.")
    return payload


def score(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, int | float]:
    """Score explicit routing, argument, dataset, caveat, relationship, and prose contracts."""
    cases_by_id = {str(item["id"]): item for item in cases}
    results_by_id = {str(item["id"]): item for item in results}
    hits = {
        "route": 0,
        "argument": 0,
        "dataset": 0,
        "caveat": 0,
        "relationship": 0,
        "required": 0,
        "prohibited": 0,
    }
    totals = {name: 0 for name in hits}

    for identifier, case in cases_by_id.items():
        result = results_by_id.get(identifier, {})
        selected = set(result.get("selected_tools", []))
        answer = str(result.get("answer", "")).casefold()
        expected = set(case.get("expected_tools", []))
        hits["route"] += len(selected & expected)
        totals["route"] += len(expected)

        tool_arguments = result.get("tool_arguments", {})
        provided_fields = {
            field
            for tool in expected
            for field in (
                tool_arguments.get(tool, {}).keys()
                if isinstance(tool_arguments.get(tool, {}), dict)
                else []
            )
        }
        expected_fields = set(case.get("expected_arguments", {}).get("required_fields", []))
        hits["argument"] += len(provided_fields & expected_fields)
        totals["argument"] += len(expected_fields)

        for label, result_field, case_field in (
            ("dataset", "datasets_used", "expected_datasets"),
            ("caveat", "caveats", "required_caveats"),
            (
                "relationship",
                "numerical_relationships",
                "expected_numerical_relationships",
            ),
        ):
            actual = set(result.get(result_field, []))
            expected_values = set(case.get(case_field, []))
            hits[label] += len(actual & expected_values)
            totals[label] += len(expected_values)

        for concept in case.get("required_concepts", []):
            totals["required"] += 1
            hits["required"] += str(concept).casefold() in answer
        for claim in case.get("prohibited_interpretations", []):
            totals["prohibited"] += 1
            hits["prohibited"] += str(claim).casefold() not in answer

    return {
        "case_count": len(cases_by_id),
        "result_count": len(results_by_id),
        "tool_route_recall": _ratio(hits["route"], totals["route"]),
        "argument_field_recall": _ratio(hits["argument"], totals["argument"]),
        "dataset_selection_recall": _ratio(hits["dataset"], totals["dataset"]),
        "required_caveat_recall": _ratio(hits["caveat"], totals["caveat"]),
        "numerical_relationship_recall": _ratio(hits["relationship"], totals["relationship"]),
        "required_concept_literal_recall": _ratio(hits["required"], totals["required"]),
        "prohibited_interpretation_literal_avoidance": _ratio(
            hits["prohibited"], totals["prohibited"]
        ),
    }


def _ratio(hits: int, total: int) -> float:
    return hits / total if total else 1.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score tool routing and textual contract checks for agent eval results."
    )
    parser.add_argument("results", type=Path)
    parser.add_argument("--cases", type=Path, default=Path("tests/evals/cases.json"))
    args = parser.parse_args()
    summary = score(_load(args.cases), _load(args.results))
    sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
