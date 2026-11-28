# SPDX-License-Identifier: MIT
"""The routing experiment must not reward malformed model output."""

from scripts.benchmark_model_routing import parse_fenced_selection, parse_selection, select_shard, summarize


def test_parse_selection_rejects_invalid_and_unknown_tools() -> None:
    available = {"get_load", "get_pool_prices"}
    assert parse_selection('["get_load"]', available) == ["get_load"]
    assert parse_selection("[]", available) == []
    assert parse_selection('["get_load", "get_load"]', available) is None
    assert parse_selection('["unknown"]', available) is None
    assert parse_selection("not JSON", available) is None
    assert parse_selection('{"tool": "get_load"}', available) is None


def test_invalid_output_is_not_correct_abstention() -> None:
    report = summarize(
        [
            {
                "expected": [],
                "selected": None,
                "input_tokens": 10,
                "output_tokens": 1,
                "wall_seconds": 0.5,
            }
        ]
    )
    assert report["exact_routes"] == 0
    assert report["correct_abstentions"] == 0
    assert report["valid_outputs"] == 0


def test_fenced_parser_is_diagnostic_only_and_rejects_prose() -> None:
    available = {"get_load"}
    fenced = '```json\n["get_load"]\n```'
    assert parse_selection(fenced, available) is None
    assert parse_fenced_selection(fenced, available) == ["get_load"]
    assert parse_fenced_selection("Explanation: " + fenced, available) is None
    assert parse_fenced_selection('```json\n["unknown"]\n```', available) is None


def test_model_shards_partition_holdout_without_leakage() -> None:
    cases = [{"id": str(i)} for i in range(12)]
    shards = [select_shard(cases, index=shard, count=4) for shard in range(4)]
    assert sorted(item["id"] for batch in shards for item in batch) == sorted(
        item["id"] for item in cases
    )
    assert all(len(batch) == 3 for batch in shards)
