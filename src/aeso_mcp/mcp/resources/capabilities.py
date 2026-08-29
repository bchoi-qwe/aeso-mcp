# SPDX-License-Identifier: MIT
"""Capabilities resource describing the server's supported domain surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


CAPABILITIES_MARKDOWN = """# AESO MCP Capabilities

This single server requires `AESO_API_KEY` and provides read-only, bounded access to Alberta
electricity market data and deterministic analytics. It does not submit bids, change AESO
records, or claim causal explanations. Transmission and market-power reports at `ets.aeso.ca`
are read through an internal source-specific client; the APIM key is never sent to that host.

## Tools

### Current and historical market data

- `get_market_snapshot` — current price, load, generation, interchange, and reserves.
- `get_pool_prices` — hourly Pool Price in CAD/MWh.
- `get_system_marginal_prices` — finer-grained System Marginal Price in CAD/MWh.
- `get_load` — Alberta Internal Load and optional forecast in MW.
- `get_generation` — current fuel mix or bounded wind/solar history in MW.
- `get_interchange` — current intertie flows in MW.
- `get_reserves` — current operating reserve indicators in MW.
- `get_outages` — hourly generator outage capacity by fuel in MW.
- `get_approved_transmission_outages` — AESO-approved planned transmission outages.
- `get_long_range_transmission_outages` — tentative long-range transmission outages.
- `get_assets` — bounded asset registry queries.
- `get_monthly_cumulative_net_revenue` — current MCSINR publication.
- `get_secondary_offer_price_limit` — current secondary offer-cap status.
- `get_historical_generation` — official individual-asset CSD history at hourly or five-minute resolution.
- `sync_historical_store` — incremental DuckDB index and partitioned Parquet snapshots.
- `get_historical_store_status` — local coverage, manifests, partitions, and schema status.
- `get_forecast` — paired typed AIL, Pool Price, wind, solar, or combined wind/solar forecasts where the selected official horizon exists.
- `get_research_data` — one typed, bounded archive read for verified historical adequacy/cushion, transmission, planning-area, constraint, EEA, OR-directive, or current participant records.
- `get_supply_adequacy` and `get_supply_surplus` — official hourly categorical status reports.
- `get_ffr_net_schedule` — historical scheduled BC/MATL intertie transfer used for FFR reporting.
- `get_dispatch_down_service` — DDS availability publications.
- `get_tmr_reference_price` — published TMR reference price by effective date.
- `get_system_events` — bounded AIES Event Log messages without inferred event endings.
- `get_uc_settlement_summary` — hourly public UC amount and charged volume.
- `get_operating_reserve_prices` — active/standby reserve price components and volumes.
- `get_operating_reserve_forecast` — current seven-day reserve-volume forecast.
- `get_operating_reserve_activations` — hourly standby reserve activations.

### Authenticated operational reports

- `get_energy_merit_order` — delayed historical energy offer blocks.
- `get_unit_commitments` — bounded generating-unit commitment directives.
- `get_generation_capacity` — hourly capability and outage components by fuel class.
- `get_load_outage_forecast` — bounded hourly load/outage forecast observations.
- `get_intertie_capability` — hourly intertie and flowgate capability and reliability margins.
- `get_intertie_outages` — intertie/flowgate outage intervals.
- `get_metered_volumes` — hourly metered energy by asset, with optional participant filters.
- `get_operating_reserve_offer_control` — delayed historical reserve offer-control blocks.
- `summarize_market_history` — compact hourly, daily, weekly, or monthly price/load statistics.
- `assess_supply_tightness` — transparent current supply-margin screening arithmetic enriched with distinct official adequacy/cushion evidence.

### Deterministic analytics

- `compare_market_periods` — aggregate Pool Price and load comparison.
- `find_price_events` — sustained high-price event detection.
- `explain_market_conditions` — structured evidence against a baseline, without causal claims.
- `compare_forecast_to_actual` — backward-compatible AIL forecast error metrics.
- `calculate_asset_energy_revenue` — matched metered MWh times Pool Price gross energy revenue.
- `compare_csd_to_metered` — operational CSD generation versus metered energy.
- `analyze_ramps` — cadence-aware AIL, net-load, renewable, and asset ramps.
- `analyze_supply_surplus_events` — descriptive market evidence during explicitly bounded surplus states.
- `get_price_statistics` and `get_price_duration_curve` — Pool Price distributions.
- `analyze_market_event` — focus/baseline price, demand, supply, offer, intertie, commitment, and reserve evidence.
- `calculate_capture_prices` — generation-weighted price by asset or fuel.
- `analyze_net_load` — AIL less selected renewable generation.
- `analyze_supply_stack` — historical offer-stack structure.
- `analyze_intertie_utilization` — gross-offer to capability proxy.
- `analyze_generation_mix` and `analyze_asset_dispatch` — CSD generation research.
- `analyze_outage_impact` — hourly outage-price association.
- `analyze_forecast_error` — generalized forecast errors for AIL, Pool Price, wind, and solar, including hour/lead-time profiles.
- `summarize_operating_reserve_market` — reserve prices, volumes, and activations by product.
- `analyze_participant_concentration` — mapped merit-order offered-volume shares and HHI with explicit unmapped blocks; not historical ownership.
- `analyze_regional_load_generation` — planning-area and regional observed load/generation aggregates.
- `analyze_constrained_volume` — constrained MWh/minutes by area/fuel and optional exact-hour price association.
- `analyze_scarcity` — categorical historical adequacy/cushion and EEA context.
- `analyze_system_frequency` — compact statistics for at most six hours of 10-second frequency data; raw rows are not exposed.

## Prompts

- `daily_market_brief` — assemble an evidence-based daily market brief.
- `investigate_price_event` — investigate a sustained Pool Price event.
- `compare_market_days` — compare two bounded market-day windows.

## Resources

All market timestamps use `America/Edmonton`; query bounds and response sizes are enforced.

- `aeso://glossary` — market terminology.
- `aeso://datasets` — dataset and upstream catalog.
- `aeso://capabilities` — this capability summary.
- `aeso://methodology/{dataset}` — interpretation notes for each supported dataset.

Methodology resources include `load`, `generation`, `generator-outages`,
`transmission-outages`, `market-power-mitigation`, `energy-merit-order`,
`unit-commitments`, `generation-capacity`, `load-outage-forecast`, `intertie-capability`,
`intertie-outages`, `metered-volume`, `operating-reserve-offer-control`, `market-history`, and
`supply-tightness`, `historical-generation`, `research-analytics`, `operating-reserve-market`,
and `uc-settlement`.
Additional methodology resources cover `official-forecasts`, `supply-adequacy`,
`supply-surplus`, `ffr-net-schedule`, `dds-tmr-system-events`, and
`official-research-data`.

Data can be operational, preliminary, forecast, tentative, or revised. Always inspect response
metadata and warnings before treating a value as final or inferring a cause.
"""


def register_capabilities_resource(mcp: FastMCP) -> None:
    """Register the server capability summary resource."""

    @mcp.resource("aeso://capabilities", mime_type="text/markdown")
    def capabilities() -> str:
        return CAPABILITIES_MARKDOWN
