# SPDX-License-Identifier: MIT
"""Unit tests for market timezone helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from aeso_mcp.errors import InvalidDateRangeError
from aeso_mcp.timeutil import (
    MARKET_TZ,
    chronological_instant,
    elapsed_hours,
    ensure_aware,
    in_half_open_range,
    is_dst_fall_back_day,
    is_dst_spring_forward_day,
    market_day_hours,
    parse_aeso_hour_ending,
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


def test_parse_aeso_hour_ending_he24_rolls_next_day() -> None:
    start, end = parse_aeso_hour_ending("08/06/2026 24")
    assert start.day == 6
    assert start.hour == 23
    assert end.day == 7
    assert end.hour == 0
    assert (to_utc(end) - to_utc(start)).total_seconds() == 3600


def test_parse_aeso_hour_ending_fall_back_starred_hour() -> None:
    # Alberta fall-back 2024-11-03 has HE02 and HE02*.
    first_start, first_end = parse_aeso_hour_ending("11/03/2024 02")
    starred_start, starred_end = parse_aeso_hour_ending("11/03/2024 02*")
    assert first_start.fold == 0
    assert starred_start.fold == 1
    assert to_utc(starred_start) > to_utc(first_start)
    assert (to_utc(first_end) - to_utc(first_start)).total_seconds() == 3600
    assert (to_utc(starred_end) - to_utc(starred_start)).total_seconds() == 3600


def test_parse_aeso_hour_ending_spring_forward_rejects_he02() -> None:
    with pytest.raises(ValueError, match="spring-forward"):
        parse_aeso_hour_ending("03/10/2024 02")


def test_validate_range_ambiguous_fall_back_uses_utc_order() -> None:
    # Chronologically later MST 01:15 must validate after earlier MDT 01:30.
    earlier = datetime(2024, 11, 3, 1, 30, tzinfo=MARKET_TZ, fold=0)
    later = datetime(2024, 11, 3, 1, 15, tzinfo=MARKET_TZ, fold=1)
    assert to_utc(later) > to_utc(earlier)
    start, end = validate_range(earlier, later)
    assert start == to_market(earlier)
    assert end == to_market(later)


def test_in_half_open_range_and_elapsed_hours_are_dst_safe() -> None:
    earlier = datetime(2024, 11, 3, 1, 30, tzinfo=MARKET_TZ, fold=0)
    later = datetime(2024, 11, 3, 1, 15, tzinfo=MARKET_TZ, fold=1)
    # Wall-clock comparison is inverted; UTC helpers must disagree with that.
    assert earlier > later
    day_end = datetime(2024, 11, 3, 3, 0, tzinfo=MARKET_TZ)
    assert in_half_open_range(later, earlier, day_end)
    assert not in_half_open_range(earlier, later, day_end)
    day_start = datetime(2024, 11, 3, 0, 0, tzinfo=MARKET_TZ)
    next_midnight = datetime(2024, 11, 4, 0, 0, tzinfo=MARKET_TZ)
    assert (next_midnight - day_start).total_seconds() / 3600 == 24.0
    assert elapsed_hours(day_start, next_midnight) == 25.0


def test_chronological_instant_orders_fall_back_fold() -> None:
    earlier = datetime(2024, 11, 3, 1, 30, tzinfo=MARKET_TZ, fold=0)
    later = datetime(2024, 11, 3, 1, 15, tzinfo=MARKET_TZ, fold=1)
    assert max([earlier, later]) is earlier  # wall-clock / fold-blind
    assert max([earlier, later], key=chronological_instant) is later
