# SPDX-License-Identifier: MIT
"""Timezone-aware market time helpers.

AESO market time is Mountain Time. We expose ``America/Edmonton`` as the
canonical zone. ``US/Mountain`` (used by GridStatus) is equivalent for Alberta.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from aeso_mcp.errors import InvalidDateRangeError

MARKET_TZ_NAME = "America/Edmonton"
MARKET_TZ = ZoneInfo(MARKET_TZ_NAME)

# AESO hour-ending labels: ``MM/DD/YYYY HH`` or ``MM/DD/YYYY HH*`` (fall-back extra hour).
_HE_LABEL_RE = re.compile(
    r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})\s+"
    r"(?P<hour>\d{1,2})(?P<star>\*)?$"
)


def ensure_aware(value: datetime, *, assume_market: bool = True) -> datetime:
    """Return a timezone-aware datetime.

    Naive values are assumed to be America/Edmonton market time unless
    ``assume_market`` is False (then UTC).
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=MARKET_TZ if assume_market else UTC)
    return value


def to_market(value: datetime) -> datetime:
    """Convert any aware/naive datetime to America/Edmonton."""
    return ensure_aware(value).astimezone(MARKET_TZ)


def to_utc(value: datetime) -> datetime:
    """Convert any aware/naive datetime to UTC."""
    return ensure_aware(value).astimezone(UTC)


def market_now() -> datetime:
    """Current time in America/Edmonton."""
    return datetime.now(tz=MARKET_TZ)


def utc_now() -> datetime:
    """Current time in UTC."""
    return datetime.now(tz=UTC)


def as_market_date(value: datetime) -> date:
    """Calendar date in market timezone."""
    return to_market(value).date()


def start_of_market_day(day: date | datetime) -> datetime:
    """Return 00:00 America/Edmonton for the given calendar day."""
    if isinstance(day, datetime):
        day = as_market_date(day)
    return datetime(day.year, day.month, day.day, tzinfo=MARKET_TZ)


def end_of_market_day(day: date | datetime) -> datetime:
    """Return exclusive end (next local midnight) for a market calendar day.

    Uses the next calendar date's midnight rather than ``timedelta(days=1)`` so
    DST spring-forward (23h) and fall-back (25h) days are represented correctly.
    """
    if isinstance(day, datetime):
        day = as_market_date(day)
    next_day = day + timedelta(days=1)
    return start_of_market_day(next_day)


def validate_range(
    start: datetime,
    end: datetime,
    *,
    max_days: float | None = None,
    label: str = "date range",
) -> tuple[datetime, datetime]:
    """Normalize and validate a half-open [start, end) range.

    Chronology and span checks use UTC so ambiguous fall-back wall times compare
    in true elapsed order.
    """
    start_m = to_market(start)
    end_m = to_market(end)
    start_utc = to_utc(start_m)
    end_utc = to_utc(end_m)
    if end_utc <= start_utc:
        raise InvalidDateRangeError(
            f"Invalid {label}: end ({end_m.isoformat()}) must be after start "
            f"({start_m.isoformat()})."
        )
    if max_days is not None:
        span_days = (end_utc - start_utc).total_seconds() / 86_400
        if span_days > max_days:
            raise InvalidDateRangeError(
                f"Invalid {label}: requested {span_days:.1f} days exceeds the maximum of "
                f"{max_days:g} days. Narrow the range or use an aggregation tool."
            )
    return start_m, end_m


def in_half_open_range(value: datetime, start: datetime, end: datetime) -> bool:
    """True if ``start <= value < end`` using UTC chronology (DST-safe)."""
    value_utc = to_utc(value)
    return to_utc(start) <= value_utc < to_utc(end)


def elapsed_hours(start: datetime, end: datetime) -> float:
    """Elapsed hours between two datetimes using UTC (DST-safe)."""
    return (to_utc(end) - to_utc(start)).total_seconds() / 3600


def format_aeso_date(value: datetime) -> str:
    """Format a market datetime as AESO API date (YYYY-MM-DD)."""
    return to_market(value).strftime("%Y-%m-%d")


def market_day_hours(day: date) -> float:
    """Number of local hours in a market calendar day (23, 24, or 25)."""
    start = start_of_market_day(day)
    end = end_of_market_day(day)
    return (to_utc(end) - to_utc(start)).total_seconds() / 3600


def is_dst_spring_forward_day(day: date) -> bool:
    """True if the local day skips an hour (23-hour day)."""
    return market_day_hours(day) == 23


def is_dst_fall_back_day(day: date) -> bool:
    """True if the local day repeats an hour (25-hour day)."""
    return market_day_hours(day) == 25


def parse_aeso_hour_ending(label: str) -> tuple[datetime, datetime]:
    """Parse an AESO hour-ending label into ``[interval_start, interval_end)``.

    Supports ``MM/DD/YYYY HH`` and fall-back ``MM/DD/YYYY HH*`` (extra hour).
    HE 24 rolls into the next calendar day's 00:00. Ambiguous fall-back hours use
    ``fold=0`` for the first occurrence and ``fold=1`` for ``*``.

    Interval length is always one UTC hour so DST transitions stay correct.
    """
    text = label.strip()
    match = _HE_LABEL_RE.match(text)
    if not match:
        raise ValueError(f"Unrecognized AESO hour-ending label: {label!r}")
    month = int(match.group("month"))
    day_n = int(match.group("day"))
    year = int(match.group("year"))
    hour = int(match.group("hour"))
    starred = match.group("star") is not None
    if hour < 1 or hour > 24:
        raise ValueError(f"AESO hour-ending hour out of range: {label!r}")
    if starred and hour != 2:
        raise ValueError(f"AESO starred hour-ending is only valid for HE 02*: {label!r}")

    day_value = date(year, month, day_n)
    if is_dst_spring_forward_day(day_value) and hour == 2:
        raise ValueError(f"AESO hour-ending {label!r} does not exist on a spring-forward day.")

    if hour == 24:
        end = start_of_market_day(day_value + timedelta(days=1))
        start = to_utc(end) - timedelta(hours=1)
        return to_market(start), to_market(end)

    fold = 1 if starred else 0
    start_hour = hour - 1
    if hour == 2 and is_dst_fall_back_day(day_value):
        start = datetime(year, month, day_n, 1, 0, tzinfo=MARKET_TZ, fold=fold)
    else:
        start = datetime(year, month, day_n, start_hour, 0, tzinfo=MARKET_TZ, fold=0)

    start_m = to_market(start)
    end_m = to_market(to_utc(start_m) + timedelta(hours=1))
    return start_m, end_m
