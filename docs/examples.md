# Investigation gallery

These recipes are designed for complete, bounded investigations rather than isolated demo calls.
They name the evidence chain, the numerical relationship to verify, and the interpretation that
must remain qualified.

## 1. Reconstruct a six-hour price event

**Question:** “Compare the six-hour Pool Price event beginning at 16:00 market time on August 1,
2026 with the preceding six hours. Rank the associated changes in load, forecast error,
capability, outages, generation mix, merit order, interties, commitments, and reserves.”

**Route:** Call `analyze_market_event` with explicit focus and baseline timestamps. Use its
structured evidence groups and ranked associations. Drill into `analyze_supply_stack` or
`analyze_intertie_utilization` only if the first result identifies a material change.

**Check:** Every absolute change must equal focus minus baseline; a percent change is valid only
when its baseline is nonzero. Treat the ranking as descriptive evidence, not a causal model.

## 2. Find and characterize sustained high-price runs

**Question:** “Find all July 2026 runs with Pool Price at or above CAD 500/MWh for at least two
consecutive hours, then compare their price and load distributions with the rest of the month.”

**Route:** Use `find_price_events`, `get_price_statistics`, and bounded follow-up calls to
`analyze_market_event` for the material runs.

**Check:** Returned event intervals must satisfy both threshold and duration. Missing hours do not
prove the price was below the threshold.

## 3. Build a price-duration research view

**Question:** “Produce July 2026 Pool Price summary statistics and a 100-point duration curve;
report the negative-price count and hours at or above CAD 100/MWh.”

**Route:** Use `get_price_statistics` and `get_price_duration_curve` over the identical half-open
interval.

**Check:** Minimum <= median <= maximum, percentile estimates remain inside that range, and curve
prices are nonincreasing as exceedance increases.

## 4. Compare renewable capture prices

**Question:** “Calculate July 2026 wind and solar capture prices and capture rates against the
same-hour market average.”

**Route:** Use `calculate_capture_prices` with `fuel_types` set to `WIND` and `SOLAR`. Inspect
matched interval counts and historical-source warnings.

**Check:** Capture price is generation-weighted Pool Price, and capture rate is capture price
divided by market-average price. Historical CSD generation is operational data, not settlement
metering or realized project revenue.

## 5. Compare three assets without overstating capacity factor

**Question:** “For three named asset IDs, compare July generation, average output, peak output,
available capacity factor, and largest hourly ramps.”

**Route:** Use `analyze_asset_dispatch` with exact asset identifiers, followed by
`get_historical_generation` only when interval-level evidence is needed.

**Check:** Capacity factor is calculated only where maximum capability is positive. An observed
hourly change is not an engineering ramp-rate limit.

## 6. Trace the net-load shape

**Question:** “Show hourly AIL, wind-plus-solar generation, and net load for the first week of July
2026; identify the maximum hourly net-load increase.”

**Route:** Use `analyze_net_load` with `renewable_fuels` set explicitly. Pair with
`analyze_generation_mix` for weekly energy shares.

**Check:** Every interval must satisfy net load = AIL - selected renewable generation. Keep MW
interval observations distinct from accumulated MWh.

## 7. Read an eligible energy supply stack

**Question:** “For HE 18 on an Energy Merit Order date that is at least 60 days old, show the
ordered offer blocks, total offered MW, dispatched MW, and marginal dispatched offer.”

**Route:** Use `analyze_supply_stack`; retrieve `get_energy_merit_order` only if the complete raw
block set is required.

**Check:** The marginal dispatched offer is the highest-priced dispatched block in the available
report. It is not, by itself, proof of what caused Pool Price.

## 8. Screen intertie offer pressure

**Question:** “Compare BC and MATL import and export gross offers with available transfer
capability over a specified month; count hours at or above a 95% offer-to-capability ratio.”

**Route:** Use `analyze_intertie_utilization`, preserving direction. Use
`get_intertie_outages` for intervals with changed capability.

**Check:** The ratio is gross offer / ATC when ATC is positive. It is an offer-to-capability proxy,
not metered flow utilization.

## 9. Test an outage-price association

**Question:** “During July 2026, were Pool Prices different in the highest quartile of generator
outage capacity? Report group means, difference, matched count, and Pearson correlation.”

**Route:** Use `analyze_outage_impact` with the automatic 75th-percentile threshold or provide an
explicit MW threshold.

**Check:** Price difference = high-outage mean - other-hours mean. A difference or correlation is
an association and does not establish that outages caused prices.

## 10. Audit AIL forecast accuracy

**Question:** “Measure AIL forecast bias, MAE, RMSE, MAPE, and market-hour error profile for a
specified month.”

**Route:** Use `analyze_forecast_error` with `series="ail"`; use `get_forecast` when individual
actual/forecast pairs are needed.

**Check:** Error = forecast - actual and RMSE >= MAE for the same finite sample. Intervals without
forecasts are excluded and must remain visible in warnings.

## 11. Separate operating-reserve price concepts

**Question:** “For August 2026, summarize active and standby reserve prices, cleared volumes, and
standby activations by regulating, spinning, and supplemental product.”

**Route:** Start with `summarize_operating_reserve_market`. Use
`get_operating_reserve_prices` and `get_operating_reserve_activations` for the supporting rows.

**Check:** Active price, standby premium, activation strike, and standby clearing blended price
are distinct. Activation prices are volume-weighted and remain CAD/MWh; procurement prices remain
CAD/MW.

## 12. Reproduce a local historical dataset

**Question:** “Persist July 2026 hourly CSD generation, Pool Price, and AIL; report coverage,
partitions, missing timestamps, and retrieval semantics.”

**Route:** Use `sync_historical_store`, then `get_historical_store_status`. Repeat the same sync to
verify that current source objects are skipped and only gaps or preliminary intervals are
refetched.

**Check:** The DuckDB index and Parquet partitions must agree on dataset/year/month boundaries.
`missing_interval_count = 0` only when every expected cadence timestamp is present. A final
replacement must supersede its preliminary interval deterministically.

## 13. Investigate fall-back daylight-saving time

**Question:** “Fetch five-minute CSD generation for one asset across the repeated fall-back hour
and show both local labels and chronological UTC instants.”

**Route:** Use `get_historical_generation` with a bounded five-minute interval and one asset ID.

**Check:** Order observations by UTC. Repeated `America/Edmonton` wall-clock labels are distinct
instants; do not assume every market day has exactly 24 local hours.

## 14. Keep commitments and settlement separate

**Question:** “For a date range, compare the count of unit-commitment directives with the hourly UC
settlement amount and charged volume.”

**Route:** Use `get_unit_commitments` for directives and `get_uc_settlement_summary` for the public
settlement report.

**Check:** Directive count, settlement CAD, and charged MW are different concepts. Do not infer a
one-to-one financial amount for each directive unless the source explicitly supplies it.

## 15. Produce a compact annual market history

**Question:** “Summarize the last twelve complete months of Pool Price and AIL into monthly
buckets, without returning every hourly observation.”

**Route:** Use `summarize_market_history` with `bucket="month"` over an explicit half-open range.

**Check:** Aggregates must use the complete internally retrieved series rather than one public
pagination page. Preserve observation counts and incomplete-source warnings.

## Investigation discipline

Begin with the narrowest summary tool that answers the question, then retrieve raw rows only for
supporting evidence. Preserve metadata, warnings, units, interval boundaries, observation type,
finality, and completeness in any downstream narrative. Use “associated with” for observational
comparisons unless a separate research design supports a causal claim.
