#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Credential-free real-model routing experiment (not a full agent-answer benchmark)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"


def parse_selection(output: str, available: set[str]) -> list[str] | None:
    """Reject malformed, invented or duplicate tool names; never execute model output."""
    try:
        value = json.loads(output.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    if len(value) != len(set(value)) or not set(value) <= available:
        return None
    return sorted(value)


def summarize(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    expected = sum(len(row["expected"]) for row in rows)
    selected = sum(len(row["selected"] or []) for row in rows)
    hits = sum(len(set(row["expected"]) & set(row["selected"] or [])) for row in rows)
    no_tool = [row for row in rows if not row["expected"]]
    return {
        "cases": len(rows),
        "valid_outputs": sum(row["selected"] is not None for row in rows),
        "exact_routes": sum(row["selected"] == row["expected"] for row in rows),
        "no_tool_cases": len(no_tool),
        "correct_abstentions": sum(row["selected"] == [] for row in no_tool),
        "route_recall": hits / expected if expected else 0.0,
        "route_precision": hits / selected if selected else 0.0,
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "wall_seconds": sum(row["wall_seconds"] for row in rows),
    }


def run(manifest: dict[str, Any], *, threads: int) -> dict[str, Any]:
    # Kept in a separate benchmark environment, never a runtime MCP dependency.
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(threads)
    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL, revision=REVISION, trust_remote_code=False, use_safetensors=True
    ).to("cpu")
    model.eval()
    descriptions = manifest["descriptions"]
    traces: dict[str, list[dict[str, Any]]] = {"full": [], "compact": []}
    for case in manifest["cases"]:
        for mode in ("full", "compact"):
            names = sorted(descriptions) if mode == "full" else case["compact_candidates"]
            catalog = "\n".join(f"{name}: {descriptions[name]}" for name in names)
            prompt = (
                "Select only the tools necessary to answer the request. "
                "If the request is unsupported or needs no tool, return []. "
                'Return only a JSON list of exact tool names, e.g. ["get_load"].\n\n'
                f"Available tools:\n{catalog}\n\nRequest: {case['question']}"
            )
            messages = [{"role": "user", "content": prompt}]
            inputs = tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True,
                return_dict=True, return_tensors="pt",
            )
            count = inputs["input_ids"].shape[-1]
            if count + 96 > model.config.max_position_embeddings:
                raise ValueError("Prompt exceeds checkpoint context; refusing silent truncation")
            started = perf_counter()
            with torch.inference_mode():
                output = model.generate(
                    **inputs, do_sample=False, max_new_tokens=96,
                    pad_token_id=tokenizer.eos_token_id,
                )
            seconds = perf_counter() - started
            generated = output[0, count:]
            answer = tokenizer.decode(generated, skip_special_tokens=True)
            traces[mode].append({
                "id": case["id"],
                "expected": sorted(case["expected_tools"]),
                "available": names,
                "selected": parse_selection(answer, set(names)),
                "raw_output": answer,
                "input_tokens": int(count),
                "output_tokens": int(len(generated)),
                "wall_seconds": seconds,
            })
    return {
        "scope": "Description-only routing; compact receives BM25(question) plus pinned tools",
        "model": MODEL,
        "revision": REVISION,
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "case_sha256": manifest["case_sha256"],
        "generation": {"do_sample": False, "max_new_tokens": 96, "cpu_threads": threads},
        "summary": {mode: summarize(rows) for mode, rows in traces.items()},
        "traces": traces,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2, choices=[1, 2, 4])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(json.loads(args.manifest.read_text(encoding="utf-8")), threads=args.threads)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(result["summary"], indent=2) + "\n")


if __name__ == "__main__":
    main()
