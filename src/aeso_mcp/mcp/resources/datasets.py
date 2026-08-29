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
| Generator Outages | `get_outages` | hourly | MW | actual | Generator Outages (aggregated by fuel via GridStatus) |
| Approved Tx Outages | `get_approved_transmission_outages` | publication | — | preliminary / approved | ETS CSV via public-reports client |
| Long-range Tx Outages | `get_long_range_transmission_outages` | publication (~24mo) | — | preliminary / tentative | ETS public report CSV |
| MCSINR | `get_monthly_cumulative_net_revenue` | hourly HE | CAD | preliminary | ETS MCSINR CSV |
| Secondary Offer Limit | `get_secondary_offer_price_limit` | publication | CAD/MWh | preliminary | ETS Current SOC CSV |
| Assets | `get_assets` | catalog | — | actual | Asset List API v1 |
| Energy Merit Order | `get_energy_merit_order` | historical hourly report | CAD/MWh, MW | final | Authenticated Energy Merit Order Report |
| Unit Commitments | `get_unit_commitments` | directive | — | preliminary | Authenticated Unit Commitment Data |
| Generation Capacity | `get_generation_capacity` | hourly by fuel class | MW | actual / forecast | Authenticated AIES Generation Capacity and Outages |
| Load Outage Forecast | `get_load_outage_forecast` | hourly | MW | forecast | Authenticated Load Outage Forecast |
| Intertie Capability | `get_intertie_capability` | hourly by path/direction | MW | preliminary | Authenticated Intertie Capability |
| Intertie Outages | `get_intertie_outages` | interval event | — | preliminary | Authenticated Intertie Outages |
| Metered Volumes | `get_metered_volumes` | hourly by asset | MWh | actual | Authenticated Metered Volumes |
| Operating Reserve Offer Control | `get_operating_reserve_offer_control` | historical hourly report | CAD/MWh, MW | final | Authenticated Operating Reserve Offer Control |
| Historical CSD Generation | `get_historical_generation` | hourly / five-minute by asset | MW | operational actual | Official AESO CSD Box archive |
| Official Forecast Series | `get_forecast` | 10-minute / hourly | MW, CAD/MWh | forecast + optional actual | AIL API; ETS Pool Price and wind/solar publications |
| Historical Adequacy / Cushion | `get_research_data` | hourly web code | categorical code | final historical forecast publication | Fixed AESO XLSX assets |
| Historical Transmission Outages | `get_research_data` | outage interval | source text | historical publication | Fixed AESO XLSX asset |
| Planning-area Load / Generation | `get_research_data` | hourly by area | MW | historical actual | Fixed yearly AESO ZIP/CSV assets |
| Constrained Volume | `get_research_data` | constrained hour by area/fuel | MWh, minutes | historical actual | Fixed AESO CSV asset |
| EEA Events / OR Directives | `get_research_data` | event / directive | level, MW, MWh, seconds | fixed historical publication | Fixed AESO XLSX assets |
| Pool Participants | `get_research_data` | current registry | — | current | Authenticated Pool Participant API |
| System Frequency | `analyze_system_frequency` | 10-second internal observations | Hz, flagged-interval seconds | historical actual | Fixed yearly AESO CSV/ZIP assets; compact output only |
| Supply Adequacy / Cushion | `get_supply_adequacy` | hourly, current seven-day publication | status codes | forecast / preliminary | ETS named report |
| Supply Surplus | `get_supply_surplus` | hourly status | status codes | forecast / preliminary | ETS named report |
| FFR Net Schedule | `get_ffr_net_schedule` | hourly | MW | final historical schedule | ETS historical CSV |
| Dispatch Down Service | `get_dispatch_down_service` | publication timestamp | MW | preliminary actual | ETS DDS report |
| TMR Reference Price | `get_tmr_reference_price` | monthly effective date | CAD/MWh | final publication | ETS TMR report |
| AIES System Events | `get_system_events` | event | — | published event log | ETS AIES Event Log |
| UC Settlement Summary | `get_uc_settlement_summary` | hourly | CAD, MW | public report | ETS UC Summary CSV |
| Operating Reserve Prices | `get_operating_reserve_prices` | daily product/time-block | CAD/MW, CAD/MWh, MW | public report | ETS active + standby price CSVs |
| Operating Reserve Forecast | `get_operating_reserve_forecast` | hourly, seven-day | MW | forecast | ETS OR forecast CSV |
| Operating Reserve Activations | `get_operating_reserve_activations` | hourly activation event | MW, CAD/MWh | actual | ETS standby activation CSV |

## Analytics (derived)

| Capability | Tool | Notes |
| --- | --- | --- |
| Period comparison | `compare_market_periods` | Pool price + load aggregates and deltas |
| Price event detection | `find_price_events` | Threshold/percentile high-price events |
| Condition evidence | `explain_market_conditions` | Structured associated changes (not causes) |
| Forecast accuracy | `compare_forecast_to_actual`, `analyze_forecast_error` | AIL plus official Pool Price / wind / solar paired errors |
| Market history summary | `summarize_market_history` | Compact price/load buckets for long windows |
| Supply tightness | `assess_supply_tightness` | Supply-margin arithmetic and screening signal |
| Supply-surplus associations | `analyze_supply_surplus_events` | Explicit event bounds aligned to observed price, load, and renewables |
| Asset energy revenue | `calculate_asset_energy_revenue` | Metered MWh times hourly Pool Price; gross energy revenue only |
| CSD versus metered | `compare_csd_to_metered` | Operational CSD MW converted by cadence versus metered MWh |
| Ramp analysis | `analyze_ramps` | Cadence-aware AIL, net-load, wind, solar, or asset ramps |
| Price statistics / duration | `get_price_statistics`, `get_price_duration_curve` | Complete hourly Pool Price series |
| Market event | `analyze_market_event` | Multi-series focus/baseline associations |
| Capture price | `calculate_capture_prices` | Hourly generation-weighted Pool Price |
| Net load | `analyze_net_load` | AIL minus selected renewable CSD generation |
| Supply stack | `analyze_supply_stack` | One-hour Energy Merit Order structure |
| Intertie proxy | `analyze_intertie_utilization` | Gross offers relative to available capability |
| Generation research | `analyze_generation_mix`, `analyze_asset_dispatch` | Fuel and individual-asset CSD analytics |
| Outage association | `analyze_outage_impact` | Hourly outage-price comparison and correlation |
| Forecast error | `analyze_forecast_error` | Bias, MAE, RMSE, denominator-aware MAPE, percentiles, hour, and lead time |
| Reserve summary | `summarize_operating_reserve_market` | Product price/volume/activation summary |
| Participant concentration | `analyze_participant_concentration` | Current-mapping offered-volume shares, HHI, and explicit unmapped blocks |
| Regional load/generation | `analyze_regional_load_generation` | Observed planning-area/region averages and load energy |
| Constrained-volume context | `analyze_constrained_volume` | Area/fuel constrained MWh/minutes and optional matched Pool Price |
| Scarcity context | `analyze_scarcity` | Categorical adequacy/cushion and EEA counts with optional price context |
| Frequency statistics | `analyze_system_frequency` | Compact six-hour statistics; threshold exposure is an upper-bound proxy |

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
