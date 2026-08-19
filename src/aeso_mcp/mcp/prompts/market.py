# SPDX-License-Identifier: MIT
"""Prompts for recurring Alberta electricity market analyses."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_market_prompts(mcp: FastMCP) -> None:
    """Register prompts for daily briefs, event investigations, and day comparisons."""

    @mcp.prompt(
        name="daily_market_brief",
        description=(
            "Prepare a concise, evidence-based Alberta electricity market brief for a market "
            "date, using current and historical AESO tools as appropriate."
        ),
    )
    def daily_market_brief(market_date: str = "today", focus: str = "all") -> str:
        return f"""Prepare a concise Alberta electricity market brief for {market_date}.

Requested focus: {focus}

Use the AESO MCP tools to retrieve evidence before writing. Start with
`get_market_snapshot` for the latest observed price, Alberta Internal Load, generation mix,
interchange, and reserves. For the requested market date, prefer `summarize_market_history` with
a daily bucket for the price/load overview; use `get_pool_prices` and `get_load` only when
interval-level context is needed. If the focus includes supply conditions, call
`assess_supply_tightness` first, then use `get_generation`, `get_generation_capacity`,
`get_outages`, or the transmission and market-power tools when the focus calls for them.

Write the brief with these sections:
1. Observed market conditions and the relevant America/Edmonton timestamps.
2. Price and demand context, including units (CAD/MWh and MW).
3. Supply, interchange, reserve, outage, or mitigation observations supported by returned data.
4. Caveats about preliminary/forecast values, missing intervals, and data finality.

Do not infer causation from correlation, fill missing observations, or present a forecast as an
actual. Name the tool and response metadata that support each material claim."""

    @mcp.prompt(
        name="investigate_price_event",
        description=(
            "Investigate sustained high Alberta Pool Price events with load and market-condition "
            "context, while keeping the result observational rather than causal."
        ),
    )
    def investigate_price_event(
        market_date: str,
        threshold_cad_per_mwh: float = 100.0,
        minimum_duration_hours: float = 1.0,
    ) -> str:
        return f"""Investigate Pool Price events for {market_date}.

Use `find_price_events` with threshold_cad_per_mwh={threshold_cad_per_mwh} and
min_duration_hours={minimum_duration_hours}. If an event is returned, retrieve the matching
`get_pool_prices` and `get_load` windows, and use `explain_market_conditions` for a focus window
versus its immediately preceding equal-length baseline when that comparison is useful.

If the event spans a long window or you need a day-level comparison, prefer
`summarize_market_history` for compact price/load statistics before retrieving raw intervals.
For supply context, call `assess_supply_tightness` and identify which inputs were observed or
missing before consulting lower-level capability or outage tools.

Report:
- event start/end, elapsed duration, peak and average price in CAD/MWh;
- load values and any available generation, interchange, reserve, outage, or mitigation evidence;
- missing or preliminary observations and the exact America/Edmonton interval boundaries.

Distinguish observed association from causation. Do not claim that a factor caused the event
unless an authoritative source explicitly establishes that relationship."""

    @mcp.prompt(
        name="compare_market_days",
        description=(
            "Compare two Alberta market days using bounded Pool Price and load statistics, with "
            "optional supply and grid context."
        ),
    )
    def compare_market_days(
        focus_date: str,
        comparison_date: str,
        metrics: str = "price and load",
    ) -> str:
        return f"""Compare the Alberta market day {focus_date} with {comparison_date}.

Requested metrics: {metrics}

Represent each day as a [00:00, 24:00) market-time window in America/Edmonton, allowing for
23-hour spring-forward and 25-hour fall-back days. Prefer `summarize_market_history` with a daily
bucket over raw series for the aligned Pool Price and Alberta Internal Load statistics; use
`compare_market_periods` for its bounded aggregate comparison when useful. Add `get_pool_prices`
or `get_load` only when interval-level detail is needed. If the requested metrics include supply
conditions, call `assess_supply_tightness`; consult generation, interchange, reserves, outage,
transmission, or market-power tools for additional context.

Present aligned statistics, absolute and percentage changes where meaningful, and the underlying
units (CAD/MWh or MW). Identify missing intervals, status/forecast qualifiers, and source
metadata. Keep the comparison descriptive: observed changes are not proof of causation."""
