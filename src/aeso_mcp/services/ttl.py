# SPDX-License-Identifier: MIT
"""Cache TTL helpers based on dataset freshness semantics."""

from __future__ import annotations

from datetime import datetime

from aeso_mcp.config import Settings
from aeso_mcp.timeutil import (
    as_market_date,
    end_of_market_day,
    market_now,
    start_of_market_day,
    to_market,
    to_utc,
)


def historical_ttl_s(settings: Settings, start: datetime, end: datetime) -> float:
    """Choose TTL for a historical query.

    Completed Alberta market days are effectively immutable and use the long
    historical TTL. Ranges that overlap the current market day use the short
    snapshot TTL so incomplete "today" observations are not frozen for hours.
    """
    today = as_market_date(market_now())
    start_m = to_market(start)
    end_m = to_market(end)
    today_start = start_of_market_day(today)
    tomorrow = end_of_market_day(today)
    overlaps_today = to_utc(start_m) < to_utc(tomorrow) and to_utc(end_m) > to_utc(today_start)
    if overlaps_today:
        return settings.cache_ttl_snapshot_s
    return settings.cache_ttl_historical_s
