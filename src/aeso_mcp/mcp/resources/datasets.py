# SPDX-License-Identifier: MIT
"""MCP resources: glossary, datasets, methodology."""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

DATASETS_MARKDOWN = """# AESO MCP Dataset Catalog

| Dataset | Tool(s) | Granularity | Units | Status | Upstream |
| --- | --- | --- | --- | --- | --- |
| Market Snapshot | `get_market_snapshot` | current | CAD/MWh, MW | actual | Current Supply Demand + prices |
| Pool Price | `get_pool_prices` | hourly | CAD/MWh | actual | Pool Price API v1.1 |
| System Marginal Price | `get_system_marginal_prices` | minute-level | CAD/MWh | actual | System Marginal Price API v1.1 |
| Alberta Internal Load | `get_load` | hourly | MW | actual / forecast | Actual/Forecast Load API |
| Generation / Fuel Mix | `get_generation` | current (all fuels); hourly wind/solar history | MW | actual | Current Supply Demand + renewable APIs |
| Interchange | `get_interchange` | current | MW | actual | Current Supply Demand API v2 |
| Operating Reserves | `get_reserves` | current | MW | actual | Current Supply Demand API v2 |
| Generator Outages | `get_outages` | hourly | MW | actual | Generator Outages (via GridStatus) |
| Approved Tx Outages | `get_approved_transmission_outages` | publication | — | preliminary / approved | ETS CSV via GridStatus |
| Long-range Tx Outages | `get_long_range_transmission_outages` | publication (~24mo) | — | preliminary / tentative | ETS public report CSV |
| MCSINR | `get_monthly_cumulative_net_revenue` | hourly HE | CAD | preliminary | ETS MCSINR CSV |
| Secondary Offer Limit | `get_secondary_offer_price_limit` | publication | CAD/MWh | preliminary | ETS Current SOC CSV |
| Assets | `get_assets` | catalog | — | actual | Asset List API v1 |

## Analytics (derived)

| Capability | Tool | Notes |
| --- | --- | --- |
| Period comparison | `compare_market_periods` | Pool price + load aggregates and deltas |
| Price event detection | `find_price_events` | Threshold/percentile high-price events |
| Condition evidence | `explain_market_conditions` | Structured associated changes (not causes) |
| Forecast accuracy | `compare_forecast_to_actual` | AIL forecast vs actual error metrics |

## Timezone
All market timestamps are normalized to **America/Edmonton**. DST spring-forward days have
23 local hours; fall-back days have 25.

## Query bounds
Server-enforced limits protect against oversized responses (for example SMP max 7 days).
Prefer analytics tools for long-period statistical questions.

## Limitations
See the repository `LIMITATIONS.md` and `docs/data-sources.md` for coverage gaps
and the APIM-first vs public-report resolution order.
"""

POOL_PRICE_METHODOLOGY = """# Methodology: Pool Price

- **Definition**: Hourly Alberta wholesale Pool Price used for energy settlement.
- **Units**: CAD/MWh
- **Interval semantics**: Each observation covers `[interval_start, interval_end)` with a
  one-hour duration in America/Edmonton, including 23- and 25-hour DST days.
- **Source**: AESO Pool Price Report via APIM (`poolprice-api/v1.1`), usually accessed through
  GridStatus's AESO client.
- **Forecast fields**: Optional forecast pool price and rolling 30-day average may appear on
  the same report; they are not settlement actuals.
- **Caveat**: Operational publications may be revised; this server does not label values as
  final settlement unless an official settlement dataset is used.
"""

SMP_METHODOLOGY = """# Methodology: System Marginal Price

- **Definition**: Real-time system marginal price that can change within an hour.
- **Units**: CAD/MWh
- **Interval semantics**: Variable-length intervals with explicit `interval_start` /
  `interval_end` (often minute-level).
- **Source**: AESO System Marginal Price Report via APIM (`systemmarginalprice-api/v1.1`).
- **Query limits**: Minute-level history is bounded (default max 7 days / observation cap).
- **Caveat**: SMP is not a substitute for hourly Pool Price settlement analysis.
"""


def register_resources(mcp: FastMCP) -> None:
    """Register stable contextual MCP resources."""

    @mcp.resource("aeso://glossary")
    def glossary() -> str:
        """AESO market terminology used by this server."""
        from aeso_mcp.mcp.resources.glossary import GLOSSARY_FALLBACK

        try:
            return (
                resources.files("aeso_mcp.data").joinpath("glossary.md").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError, ValueError):
            return GLOSSARY_FALLBACK

    @mcp.resource("aeso://datasets")
    def datasets() -> str:
        """Catalog of datasets exposed by AESO MCP."""
        return DATASETS_MARKDOWN

    @mcp.resource("aeso://methodology/pool-price")
    def pool_price_methodology() -> str:
        """Interpretation notes for Pool Price data."""
        return POOL_PRICE_METHODOLOGY

    @mcp.resource("aeso://methodology/system-marginal-price")
    def smp_methodology() -> str:
        """Interpretation notes for System Marginal Price data."""
        return SMP_METHODOLOGY
