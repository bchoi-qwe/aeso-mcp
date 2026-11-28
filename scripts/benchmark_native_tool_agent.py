#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Use Qwen's built-in function-call template with real, bounded MCP tools."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from scripts.benchmark_fixture_agent import (
    CASES,
    REVISION,
    arguments_match,
    fixture,
    numeric_match,
    parse_object,
)

MODEL = "Qwen/Qwen2.5-3B-Instruct"
CALL_PATTERN = re.compile(
    r"<tool_call>\s*([\s\S]*?)\s*</tool_call>"
    r"|<\|tool_call_start\|>\s*([\s\S]*?)\s*<\|tool_call_end\|>",
    flags=re.IGNORECASE,
)


def parse_native_tool_calls(raw: str) -> list[dict[str, Any]] | None:
    """Accept only valid Qwen-native function-call envelopes, not invented tools."""
    matches = list(CALL_PATTERN.finditer(raw))
    if not matches:
        if "<tool_call>" in raw or "<|tool_call_start|>" in raw:
            return None
        return []
    if len(matches) > 1:
        return None
    payload = matches[0].group(1) or matches[0].group(2)
    try:
        item = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(item, dict) or set(item) != {"name", "arguments"}:
        return None
    args = item["arguments"]
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if not isinstance(item["name"], str) or not isinstance(args, dict):
        return None
    return [{"name": item["name"], "arguments": args}]


def supports_abstention(raw: str) -> bool:
    """Conservatively score refusal text; not a semantic judging model."""
    clean = re.sub(r"<\|[^>]+\|>", "", raw).strip().lower()
    return any(
        phrase in clean
        for phrase in (
            "cannot guarantee",
            "can't guarantee",
            "not possible to guarantee",
            "unable to guarantee",
            "cannot predict",
            "can't predict",
            "unable to predict",
        )
    )


def run(fixture_python: Path, shard: int, total_shards: int) -> dict[str, Any]:
    if not 0 <= shard < total_shards:
        raise ValueError("Invalid native tool experiment shard")
    cases = [case for i, case in enumerate(CASES) if i % total_shards == shard]
    if not cases:
        raise ValueError("No cases for native tool experiment shard")
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    schemas = fixture(fixture_python, {"operation": "schemas"})["tools"]
    allowed = {tool["name"] for tool in schemas}
    if allowed != {"get_pool_prices", "get_load"}:
        raise ValueError("Unexpected MCP fixture tools")
    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            },
        }
        for tool in schemas
    ]
    torch.set_num_threads(2)
    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL,
        revision=REVISION,
        trust_remote_code=False,
        use_safetensors=True,
        dtype=torch.bfloat16,
    ).to("cpu")
    model.eval()

    def infer(messages: list[dict[str, Any]]) -> dict[str, Any]:
        inputs = tokenizer.apply_chat_template(
            messages,
            tools=tool_defs,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
        if inputs["input_ids"].shape[-1] + 192 > model.config.max_position_embeddings:
            raise ValueError("Native Qwen tool prompt exceeds context limit")
        started = perf_counter()
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=192,
                pad_token_id=tokenizer.eos_token_id,
            )
        new_tokens = output[0, inputs["input_ids"].shape[-1] :]
        return {
            "text": tokenizer.decode(new_tokens, skip_special_tokens=False),
            "input_tokens": int(inputs["input_ids"].shape[-1]),
            "output_tokens": len(new_tokens),
            "wall_seconds": perf_counter() - started,
        }

    traces = []
    for case in cases:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an electricity market analyst. Use public historical data tools "
                    "when the user asks for observed Pool Price or AIL. Do not invent future "
                    "guarantees. Never submit any write action. For numerical answers after a "
                    'tool response, output only JSON {"value": number, "unit": "CAD/MWh or MW"}.'
                ),
            },
            {"role": "user", "content": case["question"]},
        ]
        initial = infer(messages)
        calls = parse_native_tool_calls(initial["text"])
        raw_call = calls[0] if calls and len(calls) == 1 else None
        expected = case["tool"]
        route_ok = (
            calls == [] and expected is None and supports_abstention(initial["text"])
            if expected is None
            else bool(raw_call and raw_call["name"] == expected)
        )
        trace: dict[str, Any] = {
            "id": case["id"],
            "selection": initial,
            "parsed_native_call": raw_call,
            "valid_tool_call": calls is not None,
            "correct_route": route_ok,
            "correct_arguments": False,
            "tool_executed": False,
            "correct_numeric": False,
            "correct_end_to_end": expected is None and route_ok,
        }
        if raw_call is not None and raw_call["name"] in allowed and expected is not None:
            trace["correct_arguments"] = arguments_match(raw_call["arguments"], case)
            try:
                data = fixture(
                    fixture_python,
                    {
                        "operation": "call",
                        "name": raw_call["name"],
                        "arguments": raw_call["arguments"],
                    },
                )["result"]
            except (subprocess.SubprocessError, ValueError, KeyError, OSError) as exc:
                trace["tool_error"] = type(exc).__name__
            else:
                trace["tool_executed"] = True
                trace["tool_result"] = data
                messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": raw_call["name"],
                                    "arguments": raw_call["arguments"],
                                },
                            }
                        ],
                    }
                )
                messages.append(
                    {"role": "tool", "name": raw_call["name"], "content": json.dumps(data)}
                )
                answer = infer(messages)
                trace["answer"] = answer
                trace["correct_numeric"] = numeric_match(
                    parse_object(answer["text"].replace("<|im_end|>", "").strip()),
                    case,
                )
                trace["correct_end_to_end"] = bool(
                    route_ok and trace["correct_arguments"] and trace["correct_numeric"]
                )
        traces.append(trace)
    return {
        "model": MODEL,
        "revision": REVISION,
        "template": "checkpoint-native apply_chat_template(tools=...)",
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "shard": shard,
        "total_shards": total_shards,
        "traces": traces,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-python", type=Path, required=True)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--total-shards", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.fixture_python, args.shard, args.total_shards)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        json.dumps(
            {
                "shard": args.shard,
                "cases": len(report["traces"]),
                "correct_end_to_end": sum(row["correct_end_to_end"] for row in report["traces"]),
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
