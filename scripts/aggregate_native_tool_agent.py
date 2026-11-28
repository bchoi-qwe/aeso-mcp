#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Ensure the native-Qwen tool experiment has all five observed MCP cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.benchmark_fixture_agent import CASES, REVISION


def aggregate(root: Path, shards: int = 3) -> dict[str, object]:
    seen = set()
    rows = []
    for shard in range(shards):
        source = root / f"native-tool-{shard}" / "native-tool.json"
        report = json.loads(source.read_text(encoding="utf-8"))
        if (
            report["model"] != "Qwen/Qwen2.5-3B-Instruct"
            or report["revision"] != REVISION
            or report["shard"] != shard
            or report["total_shards"] != shards
        ):
            raise ValueError("Native tool benchmark has mismatched model or shard identity")
        for row in report["traces"]:
            if row["id"] in seen:
                raise ValueError("Duplicate native tool benchmark case")
            seen.add(row["id"])
            rows.append(row)
    if seen != {case["id"] for case in CASES}:
        raise ValueError("Incomplete native tool benchmark evidence")
    names = (
        "valid_tool_call",
        "correct_route",
        "correct_arguments",
        "tool_executed",
        "correct_numeric",
        "correct_end_to_end",
    )
    return {
        "model": "Qwen/Qwen2.5-3B-Instruct",
        "revision": REVISION,
        "cases": len(rows),
        "metrics": {name: sum(bool(row[name]) for row in rows) for name in names},
        "scope": "Actual MCP fixture with checkpoint-native function-call template",
        "limitation": "Five synthetic cases; not a production client or full benchmark",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = aggregate(args.root)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
