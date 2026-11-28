#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded real MCP execution and separately scored unseen numeric-answer cases."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from scripts.independent_agent_cases import build_cases, case_digest, shared_model_subset

MODELS = {
    "0.5b": ("Qwen/Qwen2.5-0.5B-Instruct", "7ae557604adf67be50417f59c2c2f167def9a775"),
    "1.5b": ("Qwen/Qwen2.5-1.5B-Instruct", "775b11afaf83e0dc75bd5abaf90133e47b3ec082"),
    "3b": ("Qwen/Qwen2.5-3B-Instruct", "aa8e72537993ba99e69dfaafa59ed015b17504d1"),
}
ALLOWED = {"get_pool_prices", "get_load"}


def parse_object(output: str) -> tuple[dict[str, Any] | None, bool]:
    """Return exact-JSON-or-one-fence object, with strict JSON provenance."""
    text = output.strip()
    strict = True
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        strict = False
        match = re.fullmatch(r"```(?:json)?\s*([\s\S]+?)\s*```", text, re.IGNORECASE)
        if match is None:
            return None, False
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None, False
    return (value, strict) if isinstance(value, dict) else (None, strict)


def parse_calls(selection: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if selection is None or set(selection) != {"calls"}:
        return None
    calls = selection["calls"]
    if not isinstance(calls, list) or len(calls) > 2:
        return None
    for call in calls:
        if (
            not isinstance(call, dict)
            or set(call) != {"name", "arguments"}
            or not isinstance(call["name"], str)
            or call["name"] not in ALLOWED
            or not isinstance(call["arguments"], dict)
        ):
            return None
    if len({call["name"] for call in calls}) != len(calls):
        return None
    return calls


def arguments_match(calls: list[dict[str, Any]], case: dict[str, Any]) -> bool:
    if sorted(call["name"] for call in calls) != sorted(case["expected_tools"]):
        return False
    for call in calls:
        arguments = call["arguments"]
        if set(arguments) != {"request"} or not isinstance(arguments["request"], dict):
            return False
        request = arguments["request"]
        try:
            start = datetime.fromisoformat(request["start"])
            end = datetime.fromisoformat(request["end"])
            expected_start = datetime.fromisoformat(case["start"])
            expected_end = datetime.fromisoformat(case["end"])
        except (KeyError, TypeError, ValueError):
            return False
        hours = int((expected_end - expected_start).total_seconds() / 3600)
        limit = request.get("limit", 500)
        offset = request.get("offset", 0)
        if (
            start.utcoffset() is None
            or end.utcoffset() is None
            or start != expected_start
            or end != expected_end
            or request.get("include_forecast", False) is not False
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < hours
            or offset != 0
        ):
            return False
    return True


def numeric_match(answer: dict[str, Any] | None, case: dict[str, Any]) -> bool:
    if answer is None or set(answer) != {"value", "unit"}:
        return False
    value = answer["value"]
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and math.isclose(value, case["expected_value"], abs_tol=0.025, rel_tol=0)
        and answer["unit"] == case["expected_unit"]
    )


def fixture(python: Path, command: dict[str, Any]) -> dict[str, Any]:
    # No shell, no untrusted path, no remote URL and only a fixed Python benchmark module.
    process = subprocess.run(  # noqa: S603
        [str(python.absolute()), "-m", "scripts.independent_agent_fixture"],
        input=json.dumps(command),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(process.stdout)


def run(
    fixture_python: Path, *, model_size: str, shard_index: int, shard_count: int
) -> dict[str, Any]:
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    full_cases = build_cases()
    evaluated = full_cases if model_size == "0.5b" else shared_model_subset(full_cases)
    if not 0 <= shard_index < shard_count:
        raise ValueError("Invalid independent evaluation shard")
    cases = [case for i, case in enumerate(evaluated) if i % shard_count == shard_index]
    if not cases:
        raise ValueError("Selected independent evaluation shard is empty")
    schemas = fixture(fixture_python, {"operation": "schemas"})["tools"]
    if {tool["name"] for tool in schemas} != ALLOWED:
        raise ValueError("Fixture exposes unexpected MCP tools")
    model_id, revision = MODELS[model_size]
    torch.set_num_threads(2)
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        revision=revision,
        trust_remote_code=False,
        use_safetensors=True,
        dtype=torch.float32 if model_size == "0.5b" else torch.bfloat16,
    ).to("cpu")
    model.eval()

    def infer(prompt: str, limit: int) -> dict[str, Any]:
        inputs = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
        prompt_tokens = inputs["input_ids"].shape[-1]
        if prompt_tokens + limit > model.config.max_position_embeddings:
            raise ValueError("Model prompt exceeds checkpoint context")
        started = perf_counter()
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=limit,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        new_tokens = output[0, prompt_tokens:]
        return {
            "text": tokenizer.decode(new_tokens, skip_special_tokens=True),
            "input_tokens": int(prompt_tokens),
            "output_tokens": len(new_tokens),
            "wall_seconds": perf_counter() - started,
        }

    traces = []
    for case in cases:
        selection = infer(
            "You can use up to two tools for observed historical Alberta energy data. "
            'Return ONLY JSON {"calls":[{"name":"get_pool_prices","arguments":{"request":{'
            '"start":"UTC timestamp","end":"UTC timestamp"}}}]}, or {"calls":[]} '
            "if no public historical observations can answer. Use exact names and "
            "the entire requested [start,end) UTC range. Never predict a guaranteed future, "
            "submit market offers, transfer funds or request nonpublic data. "
            f"Tool descriptions/schemas: {json.dumps(schemas)}\n"
            f"Question: {case['question']}",
            256,
        )
        parsed, strict = parse_object(selection["text"])
        calls = parse_calls(parsed)
        expected_tools = sorted(case["expected_tools"])
        route_ok = calls is not None and sorted(call["name"] for call in calls) == expected_tools
        trace: dict[str, Any] = {
            "id": case["id"],
            "category": case["category"],
            "expected_tools": expected_tools,
            "selection": selection,
            "valid_selection": calls is not None,
            "strict_json_selection": strict and calls is not None,
            "correct_route": route_ok,
            "correct_arguments": False,
            "tool_executed": False,
            "correct_numeric": False,
            "correct_end_to_end": False,
        }
        if not expected_tools:
            trace["correct_end_to_end"] = bool(route_ok and calls == [])
        elif calls:
            trace["correct_arguments"] = arguments_match(calls, case)
            try:
                responses = fixture(fixture_python, {"operation": "call", "calls": calls})["results"]
            except (subprocess.SubprocessError, KeyError, ValueError) as exc:
                trace["tool_error"] = type(exc).__name__
            else:
                trace["tool_executed"] = True
                trace["tool_result"] = responses
                answer = infer(
                    'Use ONLY these actual MCP tool results, then return ONLY JSON '
                    '{"value": number, "unit": "CAD/MWh or MW"}. For averages, '
                    "arithmetic means must divide by number of hours; load-weighted "
                    "prices divide sum(price*load) by sum(load). "
                    f"Question: {case['question']}\n"
                    f"Tool data: {json.dumps(responses)}",
                    72,
                )
                trace["answer"] = answer
                response, _ = parse_object(answer["text"])
                trace["correct_numeric"] = numeric_match(response, case)
                trace["correct_end_to_end"] = bool(
                    route_ok and trace["correct_arguments"] and trace["correct_numeric"]
                )
        traces.append(trace)
    return {
        "scope": "Separate, deterministic 54-case oracle; 16 stratified shared cases for larger models",
        "model": model_id,
        "model_size": model_size,
        "revision": revision,
        "model_dtype": str(model.dtype),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "case_sha256": case_digest(full_cases),
        "shared_case_sha256": case_digest(shared_model_subset(full_cases)),
        "shard_index": shard_index,
        "shard_count": shard_count,
        "selection_max_new_tokens": 256,
        "answer_max_new_tokens": 72,
        "traces": traces,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-size", choices=sorted(MODELS), required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--fixture-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.fixture_python,
        model_size=args.model_size,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        json.dumps(
            {
                "model": result["model"],
                "shard": result["shard_index"],
                "cases": len(result["traces"]),
                "end_to_end_correct": sum(
                    bool(t["correct_end_to_end"]) for t in result["traces"]
                ),
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
