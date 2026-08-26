# Generated MCP catalog

This file is generated from the registered FastMCP surface. Do not edit it by hand.

## Tools (47)

| Tool | Inputs | Description |
| --- | --- | --- |
| `analyze_asset_dispatch` | `request` | Summarizes official hourly CSD dispatch for selected assets: energy, average/peak generation, available capacity factor, and hourly ramp extremes. |
| `analyze_forecast_error` | `request` | Calculates AIL forecast-minus-actual bias, MAE, RMSE, MAPE, and error by market hour from paired AESO Actual Forecast Report observations. |
| `analyze_generation_mix` | `request` | Aggregates official hourly individual-asset CSD generation into energy, share, average MW, and peak MW by fuel type. |
| `analyze_intertie_utilization` | `request` | Summarizes gross offers relative to available intertie transfer capability by path and direction. This is an offer-to-capability proxy, not metered flow. |
| `analyze_market_event` | `request` | Compares structured price, demand/forecast, supply, merit-order, intertie, commitment, and operating-reserve evidence in a focus window against a supplied or immediately preceding baseline. Returns descriptive associations, not causal claims. |
| `analyze_net_load` | `request` | Calculates hourly Alberta Internal Load minus selected CSD renewable generation (wind and solar by default), including average, peak, and minimum net load. |
| `analyze_outage_impact` | `request` | Joins hourly generator outage capacity to Pool Price and reports high-outage versus other-hour price differences plus Pearson correlation as non-causal associations. |
| `analyze_supply_stack` | `request` | Analyzes one historical Energy Merit Order hour: price-sorted offer blocks, offered and dispatched MW, and the highest dispatched offer price. |
| `assess_supply_tightness` | — | Combines the current market snapshot with hourly available generation capability and outages. Returns transparent supply-margin arithmetic plus a deterministic tight/watch/comfortable screening signal; it is not an AESO declaration or a causal price explanation. |
| `calculate_capture_prices` | `request` | Joins hourly Pool Price to official CSD generation and calculates generation-weighted capture price and capture rate by requested asset and/or fuel group. |
| `compare_forecast_to_actual` | `request` | Compares Alberta Internal Load forecast versus actual over [start, end) in MW. Returns mean error, MAE, RMSE, MAPE, and paired intervals. Timestamps are America/Edmonton. |
| `compare_market_periods` | `request` | Compares aggregate pool-price and load statistics between two America/Edmonton market periods. Returns averages, min/max/median prices, load stats, and deltas. Does not assert causation. |
| `explain_market_conditions` | `request` | Returns structured evidence for market conditions in a focus window versus a baseline window (default: immediately preceding equal-length window). Includes observed metrics and associated_changes. Does not claim causation; the calling model should produce any natural-language explanation. |
| `find_price_events` | `request` | Detects sustained high Pool Price events in CAD/MWh over [start, end). Threshold may be an absolute CAD/MWh value or a percentile (default 90th). Returns event boundaries, duration, peak/average price, and load context when available. |
| `get_approved_transmission_outages` | `request` | Returns AESO-approved planned transmission outages (approval_status=approved). Omit start/end for the current public publication. Historical start/end select publication windows and are tightly bounded. Distinct from generator outages and from long-range tentative outages. |
| `get_assets` | `request` | Returns AESO market asset registry records with optional filters for asset ID, pool participant, operating status, and asset type. Results may be truncated by the limit parameter. |
| `get_energy_merit_order` | `request` | Returns one historical AESO Energy Merit Order report date as paginated offer blocks. Reports are released with a 60-day delay. Prices are CAD/MWh and volumes are MW; timestamps use America/Edmonton. |
| `get_forecast` | `request` | Returns paired AESO actual and forecast observations for a typed series over [start, end). Alberta Internal Load (series='ail') is currently supported. |
| `get_generation` | `request` | Returns Alberta generation data. Omit start/end for the current fuel-mix snapshot (all fuels, MW). Provide start and end for historical wind and solar hourly generation. Renewable share uses wind + solar + hydro over total generation. |
| `get_generation_capacity` | `request` | Returns hourly AIES maximum and available generation capability plus operating and mothball outages by fuel class. The inclusive date range is bounded to 31 days; values are MW and output is paginated. |
| `get_historical_generation` | `request` | Returns official AESO CSD individual-asset generation over [start, end), at hourly or five-minute resolution. Supports bounded asset/fuel filters and pagination. This operational CSD archive is not settlement-metered data. |
| `get_historical_store_status` | — | Reports local DuckDB/Parquet coverage, detected cadence gaps, source-file and partition counts, schema version, and whether optional storage dependencies are installed. |
| `get_interchange` | — | Returns current Alberta interchange flows by path in MW, including net interchange. Positive/negative path signs follow AESO Current Supply Demand conventions. |
| `get_intertie_capability` | `request` | Returns hourly import/export ATC, TTC, reliability margins, and gross offers for AESO interties and flowgates. Supports hour-ending bounds and optional versioned reports; date ranges are limited to 100 days and output is paginated. |
| `get_intertie_outages` | `request` | Returns outages affecting AESO interties or flowgates for an inclusive date range. Includes affected paths and event boundaries in America/Edmonton; output is paginated. |
| `get_load` | `request` | Returns Alberta Internal Load (AIL) observations in MW for [start, end). Optionally includes load forecast values when available. Timestamps are America/Edmonton. Maximum range: 90 days. |
| `get_load_outage_forecast` | `request` | Returns AESO hourly load-outage forecast observations in MW for an inclusive, maximum 31-day range. Timestamps use America/Edmonton and output is paginated. |
| `get_long_range_transmission_outages` | `request` | Returns Long Range Significant Transmission Outages covering ~24 months ahead. Entries may be tentative and not AESO-approved (approval_status=tentative). Do not confuse with get_approved_transmission_outages. |
| `get_market_snapshot` | — | Returns a cohesive current-state view of the Alberta electricity market including recent pool price, system marginal price, Alberta Internal Load, generation by fuel, net interchange, and operating reserves. Units: prices CAD/MWh, power MW. Timezone: America/Edmonton. |
| `get_metered_volumes` | `request` | Returns hourly AESO metered energy in MWh by asset. Filter by up to 20 asset IDs or 20 pool-participant IDs (not both); unfiltered requests are limited to 16 days and all output is paginated. |
| `get_monthly_cumulative_net_revenue` | `request` | Returns the current AESO Monthly Cumulative Settlement Interval Net Revenue (MCSINR) public report. Includes cumulative CAD vs 1/6 annualized unavoidable costs and whether the secondary offer price limit trigger has been reached. Timestamps: America/Edmonton hour-ending intervals. |
| `get_operating_reserve_activations` | `request` | Returns hourly standby operating-reserve activation volume and volume-weighted activation price for an inclusive market-date range. |
| `get_operating_reserve_forecast` | `request` | Returns the current public seven-day hourly forecast of active and standby regulating, spinning, and supplemental operating-reserve volumes in MW. |
| `get_operating_reserve_offer_control` | `request` | Returns one historical AESO Operating Reserve Offer Control report date as paginated blocks. Reports are released with a 60-day delay; prices are CAD/MWh, volumes are MW, and timestamps use America/Edmonton. |
| `get_operating_reserve_prices` | `request` | Returns daily active and standby operating-reserve price components and volumes by regulating, spinning, or supplemental product and time block. Active price, standby premium, activation strike, and clearing blended price remain distinct. |
| `get_outages` | `request` | Returns hourly AESO generator outage capacity by technology/fuel for [start, end) (Total Outage MW plus per-fuel components). Timestamps: America/Edmonton. For transmission planned outages use get_approved_transmission_outages or get_long_range_transmission_outages. |
| `get_pool_prices` | `request` | Returns actual AESO hourly Pool Price observations in CAD/MWh for the requested market interval [start, end). Use get_system_marginal_prices for minute-level real-time pricing. Timestamps are America/Edmonton. Maximum range: 366 days. |
| `get_price_duration_curve` | `request` | Returns an evenly sampled Pool Price duration curve sorted from highest to lowest with exceedance percentages over [start, end). |
| `get_price_statistics` | `request` | Calculates count, mean, median, range, population standard deviation, negative/high price hours, and requested percentiles from complete hourly Pool Price observations. |
| `get_reserves` | — | Returns current AESO operating reserve indicators in MW, including contingency reserve required/dispatched and fast frequency response volumes when published. |
| `get_secondary_offer_price_limit` | `request` | Returns the current AESO Secondary Offer Price Limit public report: whether the secondary offer cap is in effect and the CAD/MWh limit when posted. A null limit means the cap is not in effect. |
| `get_system_marginal_prices` | `request` | Returns AESO System Marginal Price (SMP) observations in CAD/MWh with minute-level interval boundaries for [start, end). Prefer get_pool_prices for hourly settlement prices. Timestamps are America/Edmonton. Maximum range: 7 days. |
| `get_uc_settlement_summary` | `request` | Returns the public AESO hourly Unit Commitment settlement amount in CAD and charged volume in MW for an inclusive market-date range. |
| `get_unit_commitments` | `request` | Returns AESO generating-unit commitment directives for an inclusive date range, paginated and bounded to 31 days. Includes issue, begin, operation-start, and operation-end timestamps in America/Edmonton. |
| `summarize_market_history` | `request` | Returns compact hourly, daily, weekly, or monthly Pool Price statistics and optional Alberta Internal Load statistics for [start, end). Use this before requesting raw series for long periods; summaries are capped at 400 buckets. |
| `summarize_operating_reserve_market` | `request` | Summarizes active prices or standby clearing blended prices and volumes by reserve product; optionally joins standby activations using volume-weighted activation price. |
| `sync_historical_store` | `request` | Incrementally ingests selected AESO generation, pool-price, and load datasets into the configured local DuckDB index and partitioned Parquet snapshots. Reports gaps and duplicate source observations and refreshes preliminary intervals. Requires the optional analytics dependencies. |

## Dataset and retrieval coverage

This table is generated from 30 registered retrieval tools and their live descriptions.

| Tool | Registered data contract |
| --- | --- |
| `get_approved_transmission_outages` | Returns AESO-approved planned transmission outages (approval_status=approved). Omit start/end for the current public publication. Historical start/end select publication windows and are tightly bounded. Distinct from generator outages and from long-range tentative outages. |
| `get_assets` | Returns AESO market asset registry records with optional filters for asset ID, pool participant, operating status, and asset type. Results may be truncated by the limit parameter. |
| `get_energy_merit_order` | Returns one historical AESO Energy Merit Order report date as paginated offer blocks. Reports are released with a 60-day delay. Prices are CAD/MWh and volumes are MW; timestamps use America/Edmonton. |
| `get_forecast` | Returns paired AESO actual and forecast observations for a typed series over [start, end). Alberta Internal Load (series='ail') is currently supported. |
| `get_generation` | Returns Alberta generation data. Omit start/end for the current fuel-mix snapshot (all fuels, MW). Provide start and end for historical wind and solar hourly generation. Renewable share uses wind + solar + hydro over total generation. |
| `get_generation_capacity` | Returns hourly AIES maximum and available generation capability plus operating and mothball outages by fuel class. The inclusive date range is bounded to 31 days; values are MW and output is paginated. |
| `get_historical_generation` | Returns official AESO CSD individual-asset generation over [start, end), at hourly or five-minute resolution. Supports bounded asset/fuel filters and pagination. This operational CSD archive is not settlement-metered data. |
| `get_historical_store_status` | Reports local DuckDB/Parquet coverage, detected cadence gaps, source-file and partition counts, schema version, and whether optional storage dependencies are installed. |
| `get_interchange` | Returns current Alberta interchange flows by path in MW, including net interchange. Positive/negative path signs follow AESO Current Supply Demand conventions. |
| `get_intertie_capability` | Returns hourly import/export ATC, TTC, reliability margins, and gross offers for AESO interties and flowgates. Supports hour-ending bounds and optional versioned reports; date ranges are limited to 100 days and output is paginated. |
| `get_intertie_outages` | Returns outages affecting AESO interties or flowgates for an inclusive date range. Includes affected paths and event boundaries in America/Edmonton; output is paginated. |
| `get_load` | Returns Alberta Internal Load (AIL) observations in MW for [start, end). Optionally includes load forecast values when available. Timestamps are America/Edmonton. Maximum range: 90 days. |
| `get_load_outage_forecast` | Returns AESO hourly load-outage forecast observations in MW for an inclusive, maximum 31-day range. Timestamps use America/Edmonton and output is paginated. |
| `get_long_range_transmission_outages` | Returns Long Range Significant Transmission Outages covering ~24 months ahead. Entries may be tentative and not AESO-approved (approval_status=tentative). Do not confuse with get_approved_transmission_outages. |
| `get_market_snapshot` | Returns a cohesive current-state view of the Alberta electricity market including recent pool price, system marginal price, Alberta Internal Load, generation by fuel, net interchange, and operating reserves. Units: prices CAD/MWh, power MW. Timezone: America/Edmonton. |
| `get_metered_volumes` | Returns hourly AESO metered energy in MWh by asset. Filter by up to 20 asset IDs or 20 pool-participant IDs (not both); unfiltered requests are limited to 16 days and all output is paginated. |
| `get_monthly_cumulative_net_revenue` | Returns the current AESO Monthly Cumulative Settlement Interval Net Revenue (MCSINR) public report. Includes cumulative CAD vs 1/6 annualized unavoidable costs and whether the secondary offer price limit trigger has been reached. Timestamps: America/Edmonton hour-ending intervals. |
| `get_operating_reserve_activations` | Returns hourly standby operating-reserve activation volume and volume-weighted activation price for an inclusive market-date range. |
| `get_operating_reserve_forecast` | Returns the current public seven-day hourly forecast of active and standby regulating, spinning, and supplemental operating-reserve volumes in MW. |
| `get_operating_reserve_offer_control` | Returns one historical AESO Operating Reserve Offer Control report date as paginated blocks. Reports are released with a 60-day delay; prices are CAD/MWh, volumes are MW, and timestamps use America/Edmonton. |
| `get_operating_reserve_prices` | Returns daily active and standby operating-reserve price components and volumes by regulating, spinning, or supplemental product and time block. Active price, standby premium, activation strike, and clearing blended price remain distinct. |
| `get_outages` | Returns hourly AESO generator outage capacity by technology/fuel for [start, end) (Total Outage MW plus per-fuel components). Timestamps: America/Edmonton. For transmission planned outages use get_approved_transmission_outages or get_long_range_transmission_outages. |
| `get_pool_prices` | Returns actual AESO hourly Pool Price observations in CAD/MWh for the requested market interval [start, end). Use get_system_marginal_prices for minute-level real-time pricing. Timestamps are America/Edmonton. Maximum range: 366 days. |
| `get_price_duration_curve` | Returns an evenly sampled Pool Price duration curve sorted from highest to lowest with exceedance percentages over [start, end). |
| `get_price_statistics` | Calculates count, mean, median, range, population standard deviation, negative/high price hours, and requested percentiles from complete hourly Pool Price observations. |
| `get_reserves` | Returns current AESO operating reserve indicators in MW, including contingency reserve required/dispatched and fast frequency response volumes when published. |
| `get_secondary_offer_price_limit` | Returns the current AESO Secondary Offer Price Limit public report: whether the secondary offer cap is in effect and the CAD/MWh limit when posted. A null limit means the cap is not in effect. |
| `get_system_marginal_prices` | Returns AESO System Marginal Price (SMP) observations in CAD/MWh with minute-level interval boundaries for [start, end). Prefer get_pool_prices for hourly settlement prices. Timestamps are America/Edmonton. Maximum range: 7 days. |
| `get_uc_settlement_summary` | Returns the public AESO hourly Unit Commitment settlement amount in CAD and charged volume in MW for an inclusive market-date range. |
| `get_unit_commitments` | Returns AESO generating-unit commitment directives for an inclusive date range, paginated and bounded to 31 days. Includes issue, begin, operation-start, and operation-end timestamps in America/Edmonton. |

## Prompts (3)

- `compare_market_days`
- `daily_market_brief`
- `investigate_price_event`

## Resources (24)

- `aeso://capabilities`
- `aeso://datasets`
- `aeso://glossary`
- `aeso://methodology/energy-merit-order`
- `aeso://methodology/generation`
- `aeso://methodology/generation-capacity`
- `aeso://methodology/generator-outages`
- `aeso://methodology/historical-generation`
- `aeso://methodology/intertie-capability`
- `aeso://methodology/intertie-outages`
- `aeso://methodology/load`
- `aeso://methodology/load-outage-forecast`
- `aeso://methodology/market-history`
- `aeso://methodology/market-power-mitigation`
- `aeso://methodology/metered-volume`
- `aeso://methodology/operating-reserve-market`
- `aeso://methodology/operating-reserve-offer-control`
- `aeso://methodology/pool-price`
- `aeso://methodology/research-analytics`
- `aeso://methodology/supply-tightness`
- `aeso://methodology/system-marginal-price`
- `aeso://methodology/transmission-outages`
- `aeso://methodology/uc-settlement`
- `aeso://methodology/unit-commitments`
