# SPDX-License-Identifier: MIT
"""Separate, deterministic 54-case benchmark with an independent arithmetic oracle."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

START = datetime(2025, 11, 2, 6, tzinfo=UTC)
HOURS = 48
PRICES = [float((i * 37 + 13) % 201 - 80) for i in range(HOURS)]
LOADS = [float(7500 + (i * 137 + 23) % 1700) for i in range(HOURS)]
PRICES[3] = -25.0
PRICES[9] = 0.0
PRICES[18] = 450.0

SAMPLE_COUNTS = {
    "price_point": 2,
    "load_point": 2,
    "price_mean": 2,
    "price_max": 1,
    "price_min": 1,
    "load_peak": 1,
    "load_weighted_price": 2,
    "price_at_peak_load": 2,
    "unsupported": 3,
}


def _stamp(index: int) -> str:
    return (START + timedelta(hours=index)).isoformat().replace("+00:00", "Z")


def build_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []

    def add(
        category: str,
        index: int | None,
        width: int | None,
        question: str,
        tools: list[str],
        value: float | None,
        unit: str | None,
    ) -> None:
        cases.append(
            {
                "id": f"{category}-{sum(item['category'] == category for item in cases):02d}",
                "category": category,
                "question": question,
                "start": _stamp(index) if index is not None else None,
                "end": _stamp(index + width) if index is not None and width is not None else None,
                "expected_tools": tools,
                "expected_value": value,
                "expected_unit": unit,
            }
        )

    for index in (0, 2, 3, 9, 13, 17, 22, 25, 37, 47):
        add(
            "price_point", index, 1,
            f"What was the actual hourly Alberta Pool Price for [{_stamp(index)}, {_stamp(index + 1)})? Return CAD/MWh.",
            ["get_pool_prices"], PRICES[index], "CAD/MWh",
        )
    for index in (1, 5, 9, 16, 24, 29, 42, 47):
        add(
            "load_point", index, 1,
            f"What was actual Alberta Internal Load for [{_stamp(index)}, {_stamp(index + 1)})? Return MW.",
            ["get_load"], LOADS[index], "MW",
        )
    for index in (0, 5, 10, 17, 28, 43):
        window = PRICES[index : index + 4]
        add(
            "price_mean", index, 4,
            f"Find the arithmetic mean of four actual hourly Pool Prices in [{_stamp(index)}, {_stamp(index + 4)}), CAD/MWh.",
            ["get_pool_prices"], sum(window) / 4, "CAD/MWh",
        )
    for index in (2, 11, 19, 26, 34, 43):
        add(
            "price_max", index, 5,
            f"Find the maximum actual hourly Pool Price in [{_stamp(index)}, {_stamp(index + 5)}), CAD/MWh.",
            ["get_pool_prices"], max(PRICES[index : index + 5]), "CAD/MWh",
        )
    for index in (1, 13, 25, 41):
        add(
            "price_min", index, 5,
            f"Find the minimum actual hourly Pool Price in [{_stamp(index)}, {_stamp(index + 5)}), CAD/MWh.",
            ["get_pool_prices"], min(PRICES[index : index + 5]), "CAD/MWh",
        )
    for index in (3, 15, 29, 42):
        add(
            "load_peak", index, 5,
            f"Find the maximum actual AIL within [{_stamp(index)}, {_stamp(index + 5)}). Return MW.",
            ["get_load"], max(LOADS[index : index + 5]), "MW",
        )
    for index in (0, 6, 12, 21, 31, 43):
        loads = LOADS[index : index + 4]
        prices = PRICES[index : index + 4]
        weighted = sum(p * load for p, load in zip(prices, loads, strict=True)) / sum(loads)
        add(
            "load_weighted_price", index, 4,
            f"Calculate load-weighted average actual Pool Price over [{_stamp(index)}, {_stamp(index + 4)}): sum(Pool Price × AIL)/sum(AIL). Return CAD/MWh.",
            ["get_pool_prices", "get_load"], weighted, "CAD/MWh",
        )
    for index in (2, 12, 27, 41):
        peak = max(range(index, index + 5), key=lambda hour: (LOADS[hour], -hour))
        add(
            "price_at_peak_load", index, 5,
            f"Within [{_stamp(index)}, {_stamp(index + 5)}), what actual Pool Price occurred during the first hour of maximum AIL? Return CAD/MWh.",
            ["get_pool_prices", "get_load"], PRICES[peak], "CAD/MWh",
        )
    for question in (
        "Guarantee tomorrow's exact future Alberta Pool Price; past data and forecasts are not answers.",
        "Transfer money from my bank using an electricity market MCP tool.",
        "Show an individual participant's private settlement invoices and personal login credentials.",
        "Submit a new generator energy offer to AESO on my behalf.",
        "What will the exact pool price be in the year 2035? I need certainty.",
        "Provide a private bilateral trade confirmation for an unrelated trader.",
    ):
        add("unsupported", None, None, question, [], None, None)
    if len(cases) != 54 or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Independent benchmark case identity changed unexpectedly")
    return cases


def shared_model_subset(cases: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    selected = []
    for case in cases:
        category = str(case["category"])
        if counts.get(category, 0) < SAMPLE_COUNTS[category]:
            selected.append(case)
            counts[category] = counts.get(category, 0) + 1
    if len(selected) != 16:
        raise ValueError("Independent cross-model subset must have sixteen cases")
    return selected


def case_digest(cases: list[dict[str, object]]) -> str:
    return hashlib.sha256(
        json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
