#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Aggregate actual-model/actual-MCP traces only with complete case evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scripts.benchmark_independent_agent import MODELS
from scripts.independent_agent_cases import build_cases, case_digest, shared_model_subset

FIELDS = (
    "valid_selection",
    "strict_json_selection",
    "correct_route",
    "correct_arguments",
    "tool_executed",
    "correct_numeric",
    "correct_end_to_end",
)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, Any] = {"cases": len(rows)}
    stats.update({name: sum(bool(row[name]) for row in rows) for name in FIELDS})
    absent = [row for row in rows if not row["expected_tools"]]
    stats["unsupported_cases"] = len(absent)
    stats["correct_abstentions"] = sum(bool(row["correct_end_to_end"]) for row in absent)
    stats["total_input_tokens"] = sum(
        row["selection"]["input_tokens"] + row.get("answer", {}).get("input_tokens", 0)
        for row in rows
    )
    stats["total_output_tokens"] = sum(
        row["selection"]["output_tokens"] + row.get("answer", {}).get("output_tokens", 0)
        for row in rows
    )
    stats["inference_wall_seconds"] = sum(
        row["selection"]["wall_seconds"] + row.get("answer", {}).get("wall_seconds", 0)
        for row in rows
    )
    stats["by_category"] = {
        category: {
            "cases": sum(row["category"] == category for row in rows),
            "correct_end_to_end": sum(
                row["category"] == category and bool(row["correct_end_to_end"]) for row in rows
            ),
        }
        for category in sorted({row["category"] for row in rows})
    }
    return stats


def aggregate(root: Path, shard_count: int = 6) -> dict[str, Any]:
    full = build_cases()
    shared = shared_model_subset(full)
    full_ids = {case["id"] for case in full}
    shared_ids = {case["id"] for case in shared}
    scores: dict[str, Any] = {}
    common_scores: dict[str, Any] = {}
    for size, (model, revision) in MODELS.items():
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for shard in range(shard_count):
            path = root / f"agent-{size}-{shard}" / "independent-agent.json"
            result = json.loads(path.read_text(encoding="utf-8"))
            if (
                result["model"] != model
                or result["revision"] != revision
                or result["model_size"] != size
                or result["shard_index"] != shard
                or result["shard_count"] != shard_count
                or result["case_sha256"] != case_digest(full)
                or result["shared_case_sha256"] != case_digest(shared)
            ):
                raise ValueError(f"Invalid independent model result shard: {path}")
            for row in result["traces"]:
                if row["id"] in seen:
                    raise ValueError("Duplicate independent benchmark case")
                seen.add(row["id"])
                merged.append(row)
        expected_ids = full_ids if size == "0.5b" else shared_ids
        if seen != expected_ids:
            raise ValueError(f"Incomplete independent model evaluation for {model}")
        scores[size] = summarize(merged)
        common_scores[size] = summarize([row for row in merged if row["id"] in shared_ids])
    return {
        "description": "Actual MCP execution against synthetic 48-hour data; deterministic independent oracle",
        "full_case_sha256": case_digest(full),
        "shared_case_sha256": case_digest(shared),
        "full_model_scores": scores,
        "same_sixteen_case_comparison": common_scores,
        "constraint": "Not representative of real-world agent accuracy or non-synthetic AESO responses",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = aggregate(args.root)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
