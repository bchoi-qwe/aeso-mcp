# SPDX-License-Identifier: MIT
"""Validate native Qwen tool-envelope parsing before costly inference."""

from scripts.benchmark_native_tool_agent import parse_native_tool_calls, supports_abstention


def test_native_tool_call_accepts_json_object_or_string_arguments() -> None:
    native = (
        '<tool_call>{"name":"get_pool_prices","arguments":'
        '{"request":{"start":"2025-01-15T07:00:00Z","end":"2025-01-15T08:00:00Z"}}}</tool_call>'
    )
    result = parse_native_tool_calls(native)
    assert result is not None and result[0]["name"] == "get_pool_prices"
    assert result[0]["arguments"]["request"]["start"].endswith("Z")

    legacy = (
        '<|tool_call_start|>{"name":"get_load","arguments":"{\\"request\\":{}}" }'
        "<|tool_call_end|>"
    )
    parsed = parse_native_tool_calls(legacy)
    assert parsed is not None and parsed[0]["arguments"] == {"request": {}}


def test_native_tool_call_rejects_unsafe_or_malformed_syntax() -> None:
    assert parse_native_tool_calls("<tool_call>{oops}</tool_call>") is None
    assert parse_native_tool_calls("<tool_call>{}") is None
    assert parse_native_tool_calls("no call") == []
    assert not supports_abstention("The future Pool Price will be exactly 123.")
    assert supports_abstention("I cannot guarantee tomorrow's price.")
