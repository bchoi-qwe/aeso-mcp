# SPDX-License-Identifier: MIT
"""Cache TTL helpers based on dataset freshness semantics."""

from __future__ import annotations

from datetime import datetime

from aeso_mcp.config import Settings
from aeso_mcp.timeutil import (
    as_market_date,
    market_now,
    start_of_market_day,
    to_utc,
)


def historical_ttl_s(settings: Settings, start: datetime, end: datetime) -> float:
    """Choose TTL for a historical query.

    Completed Alberta market days are effectively immutable and use the long
    historical TTL. Ranges that overlap the current market day or extend into
    the future use the short snapshot TTL so incomplete observations are not
    frozen for hours.
    """
    today = as_market_date(market_now())
    today_start = start_of_market_day(today)
    if to_utc(end) > to_utc(today_start):
        return settings.cache_ttl_snapshot_s
    return settings.cache_ttl_historical_s
