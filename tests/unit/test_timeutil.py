# SPDX-License-Identifier: MIT
"""Unit tests for market timezone helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from aeso_mcp.errors import InvalidDateRangeError
from aeso_mcp.timeutil import (
    MARKET_TZ,
    ensure_aware,
    is_dst_fall_back_day,
    is_dst_spring_forward_day,
    market_day_hours,
    start_of_market_day,
    to_market,
    to_utc,
    validate_range,
)


def test_naive_datetime_assumed_edmonton() -> None:
    naive = datetime(2024, 1, 15, 12, 0, 0)
    aware = ensure_aware(naive)
    assert aware.tzinfo is not None
    assert aware.tzinfo == MARKET_TZ


def test_utc_to_edmonton_conversion() -> None:
    utc_dt = datetime(2024, 1, 15, 19, 0, 0, tzinfo=UTC)
    local = to_market(utc_dt)
    assert local.hour == 12  # MST in January


def test_edmonton_to_utc() -> None:
    local = datetime(2024, 1, 15, 12, 0, 0, tzinfo=MARKET_TZ)
    utc = to_utc(local)
    assert utc.tzinfo == UTC
    assert utc.hour == 19


def test_dst_spring_forward_23_hour_day() -> None:
    # Second Sunday in March 2024 was March 10 in Alberta
    day = date(2024, 3, 10)
    assert is_dst_spring_forward_day(day)
    assert market_day_hours(day) == 23
    assert not is_dst_fall_back_day(day)


def test_dst_fall_back_25_hour_day() -> None:
    # First Sunday in November 2024 was November 3 in Alberta
    day = date(2024, 11, 3)
    assert is_dst_fall_back_day(day)
    assert market_day_hours(day) == 25
    assert not is_dst_spring_forward_day(day)


def test_regular_day_24_hours() -> None:
    day = date(2024, 6, 15)
    assert market_day_hours(day) == 24
    assert not is_dst_spring_forward_day(day)
    assert not is_dst_fall_back_day(day)


def test_validate_range_rejects_inverted() -> None:
    start = datetime(2024, 1, 2, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    with pytest.raises(InvalidDateRangeError):
        validate_range(start, end)


def test_validate_range_enforces_max_days() -> None:
    start = datetime(2024, 1, 1, tzinfo=MARKET_TZ)
    end = datetime(2024, 2, 15, tzinfo=MARKET_TZ)
    with pytest.raises(InvalidDateRangeError, match="maximum"):
        validate_range(start, end, max_days=7)


def test_start_of_market_day_midnight_boundary() -> None:
    start = start_of_market_day(date(2025, 1, 1))
    assert start.hour == 0
    assert start.minute == 0
    assert start.year == 2025
