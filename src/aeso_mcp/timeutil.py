# SPDX-License-Identifier: MIT
"""Timezone-aware market time helpers.

AESO market time is Mountain Time. We expose ``America/Edmonton`` as the
canonical zone. ``US/Mountain`` (used by GridStatus) is equivalent for Alberta.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from aeso_mcp.errors import InvalidDateRangeError

MARKET_TZ_NAME = "America/Edmonton"
MARKET_TZ = ZoneInfo(MARKET_TZ_NAME)


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
    """Normalize and validate a half-open [start, end) range."""
    start_m = to_market(start)
    end_m = to_market(end)
    if end_m <= start_m:
        raise InvalidDateRangeError(
            f"Invalid {label}: end ({end_m.isoformat()}) must be after start ({start_m.isoformat()})."
        )
    if max_days is not None:
        span_days = (end_m - start_m).total_seconds() / 86_400
        if span_days > max_days:
            raise InvalidDateRangeError(
                f"Invalid {label}: requested {span_days:.1f} days exceeds the maximum of "
                f"{max_days:g} days. Narrow the range or use an aggregation tool."
            )
    return start_m, end_m


def format_aeso_date(value: datetime) -> str:
    """Format a market datetime as AESO API date (YYYY-MM-DD)."""
    return to_market(value).strftime("%Y-%m-%d")


def market_day_hours(day: date) -> float:
    """Number of local hours in a market calendar day (23, 24, or 25)."""
    start = start_of_market_day(day)
    end = end_of_market_day(day)
    # Same tzinfo instances are subtracted as naive wall times by datetime;
    # convert to UTC to measure true elapsed hours across DST transitions.
    return (to_utc(end) - to_utc(start)).total_seconds() / 3600


def is_dst_spring_forward_day(day: date) -> bool:
    """True if the local day skips an hour (23-hour day)."""
    return market_day_hours(day) == 23


def is_dst_fall_back_day(day: date) -> bool:
    """True if the local day repeats an hour (25-hour day)."""
    return market_day_hours(day) == 25
