#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Credential-free real-model routing experiment (not a full agent-answer benchmark)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

MODELS = {
    "0.5b": ("Qwen/Qwen2.5-0.5B-Instruct", "7ae557604adf67be50417f59c2c2f167def9a775"),
    "1.5b": ("Qwen/Qwen2.5-1.5B-Instruct", "775b11afaf83e0dc75bd5abaf90133e47b3ec082"),
    "3b": ("Qwen/Qwen2.5-3B-Instruct", "aa8e72537993ba99e69dfaafa59ed015b17504d1"),
}


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


def parse_fenced_selection(output: str, available: set[str]) -> list[str] | None:
    """Secondary diagnostic: accept one exact JSON fence, never surrounding prose."""
    strict = parse_selection(output, available)
    if strict is not None:
        return strict
    match = re.fullmatch(r"```(?:json)?\s*([\s\S]+?)\s*```", output.strip(), flags=re.IGNORECASE)
    return parse_selection(match.group(1), available) if match else None


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


def select_shard(cases: list[dict[str, Any]], *, index: int, count: int) -> list[dict[str, Any]]:
    if not 0 <= index < count or count <= 0:
        raise ValueError("Invalid model evaluation shard")
    return [case for i, case in enumerate(cases) if i % count == index]


def run(
    manifest: dict[str, Any],
    *,
    threads: int,
    model_size: str = "0.5b",
    shard_index: int = 0,
    shard_count: int = 1,
) -> dict[str, Any]:
    # Kept in a separate benchmark environment, never a runtime MCP dependency.
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(threads)
    model_id, revision = MODELS[model_size]
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        revision=revision,
        trust_remote_code=False,
        use_safetensors=True,
        dtype=torch.bfloat16 if model_size != "0.5b" else torch.float32,
    ).to("cpu")
    model.eval()
    descriptions = manifest["descriptions"]
    traces: dict[str, list[dict[str, Any]]] = {"full": [], "compact": []}
    shard_cases = select_shard(manifest["cases"], index=shard_index, count=shard_count)
    if not shard_cases:
        raise ValueError("Selected model evaluation shard is empty")
    for case in shard_cases:
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
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
                return_tensors="pt",
            )
            count = inputs["input_ids"].shape[-1]
            if count + 96 > model.config.max_position_embeddings:
                raise ValueError("Prompt exceeds checkpoint context; refusing silent truncation")
            started = perf_counter()
            with torch.inference_mode():
                output = model.generate(
                    **inputs,
                    do_sample=False,
                    max_new_tokens=96,
                    pad_token_id=tokenizer.eos_token_id,
                )
            seconds = perf_counter() - started
            generated = output[0, count:]
            answer = tokenizer.decode(generated, skip_special_tokens=True)
            traces[mode].append(
                {
                    "id": case["id"],
                    "expected": sorted(case["expected_tools"]),
                    "available": names,
                    "selected": parse_selection(answer, set(names)),
                    "fenced_selection": parse_fenced_selection(answer, set(names)),
                    "raw_output": answer,
                    "input_tokens": int(count),
                    "output_tokens": len(generated),
                    "wall_seconds": seconds,
                }
            )
    return {
        "scope": "Description-only routing; compact receives BM25(question) plus pinned tools",
        "model": model_id,
        "revision": revision,
        "dtype": str(model.dtype),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "case_sha256": manifest["case_sha256"],
        "shard_count": shard_count,
        "shard_index": shard_index,
        "generation": {"do_sample": False, "max_new_tokens": 96, "cpu_threads": threads},
        "summary": {mode: summarize(rows) for mode, rows in traces.items()},
        "fenced_summary": {
            mode: summarize([{**row, "selected": row["fenced_selection"]} for row in rows])
            for mode, rows in traces.items()
        },
        "traces": traces,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2, choices=[1, 2, 4])
    parser.add_argument("--model-size", choices=sorted(MODELS), default="0.5b")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        json.loads(args.manifest.read_text(encoding="utf-8")),
        threads=args.threads,
        model_size=args.model_size,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        json.dumps({"strict": result["summary"], "fenced": result["fenced_summary"]}, indent=2)
        + "\n"
    )


if __name__ == "__main__":
    main()
