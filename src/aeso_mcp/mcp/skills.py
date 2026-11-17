# SPDX-License-Identifier: MIT
"""Optional agent guidance served as ordinary MCP resources with FastMCP 4.2."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Skill
from fastmcp.skills.provider import SkillCatalogProvider

if TYPE_CHECKING:
    from fastmcp import FastMCP


SKILL_INSTRUCTIONS = """# Alberta Electricity Market Research

Use this workflow to investigate Alberta electricity-market questions through
the aeso-mcp tools. The server is independent and not affiliated with AESO.

## 1. Select the smallest authoritative data source

- For current conditions use `get_market_snapshot`.
- For hourly settlement prices in CAD/MWh use `get_pool_prices`; minute-level
  `get_system_marginal_prices` (SMP) is a **different** price series.
- For demand and generator output use `get_load`, `get_generation`, or
  `get_historical_generation`. Historical CSD operational MW is not metered
  settlement MWh; do not treat them as interchangeable.
- For official forecasts use `get_forecast`, specifying the series and date range.
  If assessing real-world predictive performance, use `as_of` to select a
  historically available publication; never score a revised or later forecast
  as though it had been known in advance.
- For specialized datasets, use `search_aeso_tools` to locate the correct
  report and check `aeso://datasets` and matching methodology resources.
- Use `analyze_market_event` and other deterministic analytics only for their
  declared statistical calculations, not as a substitute for source observations.

## 2. Respect market time and units

- Report market timestamps in `America/Edmonton`, retaining the UTC offset.
  Daylight-saving transitions can produce 23- or 25-hour local dates; use
  timezone-aware endpoints and compare exact instants when joining datasets.
- Preserve measurement intervals and dimensional units: CAD/MWh for prices,
  MW for power, and MWh for interval energy. Do not directly multiply MW
  by CAD/MWh without an aligned duration conversion to MWh.
- Match interval cadence, market dates, and asset identifiers before computing
  correlations, revenues, capture prices, or forecast errors.
- Prefer concise time windows and paginate large datasets rather than assuming
  the server returns every observation in one call.

## 3. Check provenance, finality, and missingness

- Inspect `metadata`, `warnings`, `completeness`, `source_product`,
  `retrieved_at`, data status, and publication-vintage fields before drawing
  conclusions. A partial API result is not a complete market history.
- Distinguish preliminary, final, revised, historical, forecast, and missing
  observations. Report unavailable data instead of inventing replacements.
- Distinguish the forecast issue/publication time from the target delivery
  interval. Exclude look-ahead leakage from backtests.
- Public reports and CSD archives can work without a key, but authenticated
  APIM datasets require `AESO_API_KEY`. Never request a credential through
  an MCP tool or include secrets in research output.

## 4. Explain evidence, not unsupported causation

- State the dataset(s), date/time window, units, and calculation used.
- Identify major data gaps, source delays, mismatched resolution, and uncertainty.
- Correlation between a price move and supply/outages/flows does not establish
  the cause. Describe it as an observed association without stronger evidence.
- For trading or settlement questions, note that derived gross energy revenue
  omits fees, hedges, ancillary revenues, and other costs unless explicitly
  modeled. Do not present research proxies as realized P&L.
- If a claim cannot be checked with available observations, say so.
"""


def register_market_research_skill(mcp: FastMCP) -> None:
    """Publish one shared guide for domain-specific agent workflows."""
    skill = Skill(
        name="alberta-market-research",
        description=(
            "Investigate Alberta electricity market prices, load, generation, "
            "forecasts, and events using official AESO sources, point-in-time "
            "vintages, consistent units, and transparent limitations."
        ),
        instructions=SKILL_INSTRUCTIONS,
    )
    mcp.add_provider(SkillCatalogProvider([skill]))
