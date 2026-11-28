#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reject missing, repeated or mismatched model-routing shards before scoring."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.benchmark_model_routing import MODELS, summarize


def aggregate(
    *,
    root: Path,
    cases_path: Path,
    model_sizes: tuple[str, ...] = ("1.5b", "3b"),
    shard_count: int = 4,
) -> dict[str, Any]:
    case_bytes = cases_path.read_bytes()
    expected_ids = {case["id"] for case in json.loads(case_bytes)}
    if len(expected_ids) != 12:
        raise ValueError("Unexpected independent routing case count")
    all_files = sorted(root.glob("model-routing-*/model-routing.json"))
    if len(all_files) != len(model_sizes) * shard_count:
        raise ValueError("Missing or surplus model-routing shard artifacts")
    outputs: dict[str, dict[str, Any]] = {}
    for model_size in model_sizes:
        expected_model, revision = MODELS[model_size]
        rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen: dict[str, set[str]] = defaultdict(set)
        for shard in range(shard_count):
            path = root / f"model-routing-{model_size}-{shard}" / "model-routing.json"
            result = json.loads(path.read_text(encoding="utf-8"))
            if (
                result["model"] != expected_model
                or result["revision"] != revision
                or result["case_sha256"] != hashlib.sha256(case_bytes).hexdigest()
                or result["shard_count"] != shard_count
                or result["shard_index"] != shard
            ):
                raise ValueError(f"Inconsistent routing evidence in {path}")
            for mode in ("full", "compact"):
                for row in result["traces"][mode]:
                    case_id = row["id"]
                    if case_id in seen[mode] or case_id not in expected_ids:
                        raise ValueError(f"Duplicated/unknown routing case: {case_id}")
                    seen[mode].add(case_id)
                    rows[mode].append(row)
        if any(seen[mode] != expected_ids for mode in ("full", "compact")):
            raise ValueError("Not all routing cases were evaluated in both modes")
        outputs[model_size] = {
            "model": expected_model,
            "revision": revision,
            "strict": {mode: summarize(rows[mode]) for mode in ("full", "compact")},
            "fenced_diagnostic": {
                mode: summarize(
                    [{**row, "selected": row["fenced_selection"]} for row in rows[mode]]
                )
                for mode in ("full", "compact")
            },
        }
    return {
        "case_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "total_cases_per_model": len(expected_ids),
        "models": outputs,
        "scope": "Routing choices only; descriptive tool catalog, not complete agent execution",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = aggregate(root=args.root, cases_path=args.cases)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
