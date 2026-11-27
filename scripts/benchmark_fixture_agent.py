#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Model-generated tool arguments and numeric answers on a bounded synthetic fixture."""

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

MODEL = "Qwen/Qwen2.5-3B-Instruct"
REVISION = "aa8e72537993ba99e69dfaafa59ed015b17504d1"

CASES = [
    {
        "id": "zero-price",
        "question": "What was the observed hourly Pool Price from 2025-01-15T08:00:00Z to 2025-01-15T09:00:00Z?",
        "tool": "get_pool_prices",
        "start": "2025-01-15T08:00:00Z",
        "end": "2025-01-15T09:00:00Z",
        "value": 0.0,
        "unit": "CAD/MWh",
    },
    {
        "id": "negative-price",
        "question": "What was the observed hourly Pool Price from 2025-01-15T07:00:00Z to 2025-01-15T08:00:00Z?",
        "tool": "get_pool_prices",
        "start": "2025-01-15T07:00:00Z",
        "end": "2025-01-15T08:00:00Z",
        "value": -10.0,
        "unit": "CAD/MWh",
    },
    {
        "id": "mean-price",
        "question": "Compute the arithmetic mean of the three observed hourly Pool Prices from 2025-01-15T07:00:00Z to 2025-01-15T10:00:00Z.",
        "tool": "get_pool_prices",
        "start": "2025-01-15T07:00:00Z",
        "end": "2025-01-15T10:00:00Z",
        "value": 10.0,
        "unit": "CAD/MWh",
    },
    {
        "id": "load",
        "question": "What was the Alberta Internal Load from 2025-01-15T09:00:00Z to 2025-01-15T10:00:00Z?",
        "tool": "get_load",
        "start": "2025-01-15T09:00:00Z",
        "end": "2025-01-15T10:00:00Z",
        "value": 9000.0,
        "unit": "MW",
    },
    {
        "id": "unsupported",
        "question": "Guarantee tomorrow's future pool price. Historical observations are not an answer.",
        "tool": None,
    },
]


def parse_object(text: str) -> dict[str, Any] | None:
    """Accept a bare JSON object or one exact enclosing JSON fence."""
    content = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*([\s\S]+?)\s*```", content, flags=re.IGNORECASE)
    if match:
        content = match.group(1)
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def arguments_match(arguments: Any, case: dict[str, Any]) -> bool:
    try:
        request = arguments["request"]
        start = datetime.fromisoformat(request["start"])
        end = datetime.fromisoformat(request["end"])
        return (
            start.utcoffset() is not None
            and end.utcoffset() is not None
            and start == datetime.fromisoformat(case["start"])
            and end == datetime.fromisoformat(case["end"])
        )
    except (KeyError, ValueError, TypeError):
        return False


def numeric_match(answer: dict[str, Any] | None, case: dict[str, Any]) -> bool:
    if answer is None or set(answer) != {"value", "unit"}:
        return False
    value = answer["value"]
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and math.isclose(value, case["value"], rel_tol=0, abs_tol=1e-6)
        and answer["unit"] == case["unit"]
    )


def fixture(python: Path, command: dict[str, Any]) -> dict[str, Any]:
    process = subprocess.run(  # noqa: S603
        [str(python.absolute()), "-m", "scripts.fixture_agent_tools"],
        input=json.dumps(command),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(process.stdout)


def run(python: Path) -> dict[str, Any]:
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tools = fixture(python, {"operation": "schemas"})["tools"]
    allowed = {tool["name"] for tool in tools}
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

    def infer(prompt: str) -> dict[str, Any]:
        inputs = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
        count = inputs["input_ids"].shape[-1]
        if count + 192 > model.config.max_position_embeddings:
            raise ValueError("Prompt exceeds checkpoint context")
        started = perf_counter()
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=192,
                pad_token_id=tokenizer.eos_token_id,
            )
        elapsed = perf_counter() - started
        generated = output[0, count:]
        return {
            "text": tokenizer.decode(generated, skip_special_tokens=True),
            "input_tokens": int(count),
            "output_tokens": len(generated),
            "seconds": elapsed,
        }

    traces = []
    for case in CASES:
        selection = infer(
            "Choose a tool using the schemas, or abstain if neither can answer. "
            'Return only JSON with keys "tool" and "arguments". The tool is its exact name '
            'and arguments must conform to its input schema. If unsupported, return '
            '{"tool":null,"arguments":{}}. Use the explicit UTC timestamps in the question.\n'
            f"Tools: {json.dumps(tools)}\nQuestion: {case['question']}"
        )
        call = parse_object(selection["text"])
        valid = (
            call is not None
            and set(call) == {"tool", "arguments"}
            and (call["tool"] is None or isinstance(call["tool"], str))
            and isinstance(call["arguments"], dict)
        )
        route_ok = bool(valid and call is not None and call["tool"] == case["tool"])
        trace = {
            "id": case["id"],
            "selection": selection,
            "route_correct": route_ok,
            "arguments_correct": False,
            "tool_executed": False,
            "numeric_correct": False,
            "end_to_end_correct": False,
        }
        if case["tool"] is None:
            trace["end_to_end_correct"] = bool(
                route_ok and call is not None and call["arguments"] == {}
            )
        elif valid and call is not None and call["tool"] in allowed:
            trace["arguments_correct"] = arguments_match(call["arguments"], case)
            try:
                data = fixture(
                    python,
                    {"operation": "call", "name": call["tool"], "arguments": call["arguments"]},
                )["result"]
            except (ValueError, subprocess.SubprocessError, KeyError) as exc:
                trace["tool_error"] = type(exc).__name__
            else:
                trace["tool_executed"] = True
                trace["tool_result"] = data
                answer = infer(
                    "Answer using only the supplied synthetic tool output. "
                    'Return only JSON {"value": number, "unit": "CAD/MWh or MW"}. '
                    "Use the applicable unit exactly and calculate the requested value.\n"
                    f"Question: {case['question']}\nTool output: {json.dumps(data)}"
                )
                trace["answer"] = answer
                trace["numeric_correct"] = numeric_match(parse_object(answer["text"]), case)
                trace["end_to_end_correct"] = (
                    route_ok and trace["arguments_correct"] and trace["numeric_correct"]
                )
        traces.append(trace)
    metrics = {
        "cases": len(traces),
        "numeric_cases": len(traces) - 1,
        **{
            key: sum(bool(row[key]) for row in traces)
            for key in (
                "route_correct",
                "arguments_correct",
                "tool_executed",
                "numeric_correct",
                "end_to_end_correct",
            )
        },
    }
    return {
        "scope": "Two actual MCP tools with synthetic provider; five fixed pilot questions",
        "model": MODEL,
        "revision": REVISION,
        "dtype": str(model.dtype),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "cases": CASES,
        "metrics": metrics,
        "traces": traces,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.fixture_python)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(result["metrics"], indent=2) + "\n")


if __name__ == "__main__":
    main()
