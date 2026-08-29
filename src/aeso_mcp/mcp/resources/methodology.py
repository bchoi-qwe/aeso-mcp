# SPDX-License-Identifier: MIT
"""Methodology notes for AESO datasets exposed by the MCP server."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


METHODOLOGY_MARKDOWN: dict[str, str] = {
    "load": """# Methodology: Alberta Internal Load

- **Definition**: Alberta Internal Load (AIL) is the electricity demand measure reported for the
  Alberta Interconnected Electric System.
- **Units**: MW. Values are observations for explicit `[interval_start, interval_end)` windows in
  `America/Edmonton`.
- **Source**: AESO Actual/Forecast Load data, accessed through the GridStatus provider.
- **Forecast semantics**: `load_forecast_mw`, when present, is a forecast and not an observed
  actual. Compare the two explicitly with `compare_forecast_to_actual`.
- **Caveat**: Operational publications can be revised and may contain missing intervals. The
  response metadata status and warnings take precedence over assumptions about finality.
""",
    "generation": """# Methodology: Generation and Fuel Mix

- **Current snapshot**: `get_generation` returns the latest published generation by fuel in MW,
  with an observed timestamp and a computed renewable share when the required components exist.
- **Historical series**: Historical requests currently cover hourly wind and solar generation;
  each row has explicit interval boundaries in `America/Edmonton`.
- **Source**: AESO Current Supply Demand for the snapshot and AESO renewable-generation datasets
  through the GridStatus provider for historical series.
- **Interpretation**: Fuel labels are source categories. Do not treat a missing category as zero;
  inspect the returned components, status, and warnings.
- **Caveat**: Generation and load are operational observations, not a statement about settlement
  or dispatch causality.
""",
    "generator-outages": """# Methodology: Generator Outages

- **Definition**: Hourly unavailable generator capacity aggregated by fuel or technology.
- **Units**: MW of outage capacity, including `total_outage_mw` and the published per-fuel
  components.
- **Source**: Generator Outages data provided through GridStatus's AESO adapter.
- **Intervals**: Results use `[interval_start, interval_end)` in `America/Edmonton`; query bounds
  are enforced to keep responses bounded.
- **Interpretation**: An outage-capacity observation is not a commitment, dispatch instruction, or
  proof that the outage caused a price movement. Check publication time and response warnings.
""",
    "transmission-outages": """# Methodology: Transmission Outages

- **Approved planned outages**: `get_approved_transmission_outages` reads the current AESO public
  report and returns records marked `approval_status=approved`.
- **Long-range outages**: `get_long_range_transmission_outages` reads the separate long-range
  report. These records are planning information and are marked `approval_status=tentative`.
- **Fields**: Records can include owner, element, scheduled activity, interval, publication time,
  comments, and interconnection. Empty fields mean the source did not publish a value.
- **Source**: AESO public reports at `ets.aeso.ca`, read by an internal source-specific
  public-reports HTTP client rather than the authenticated APIM client. The single MCP server
  still requires `AESO_API_KEY`; the key is never sent to `ets.aeso.ca`.
- **Caveat**: A planned or tentative outage is not evidence of a realized physical outage or a
  causal price effect. Use approval status and publication time in any interpretation.
""",
    "market-power-mitigation": """# Methodology: Market-Power Mitigation

- **MCSINR**: `get_monthly_cumulative_net_revenue` returns the current Monthly Cumulative
  Settlement Interval Net Revenue publication, including cumulative revenue, one-sixth
  annualized unavoidable costs, and the published trigger state where available.
- **Secondary offer cap**: `get_secondary_offer_price_limit` returns whether the secondary offer
  price limit is in effect and the CAD/MWh limit when one is published. A null limit is not a zero
  price; it means no numeric limit was posted in that record.
- **Source**: AESO public reports at `ets.aeso.ca`, read by the internal source-specific
  public-reports HTTP client; the single MCP server still requires `AESO_API_KEY`, which is not
  sent to this host.
- **Interpretation**: These are public operational/mitigation publications. They do not by
  themselves establish participant conduct, market power, or the cause of a price event.
""",
    "energy-merit-order": """# Methodology: Energy Merit Order

- **Definition**: Historical submitted energy offer blocks ordered by interval and offer price,
  including asset, block, volume, dispatch, and offer-control fields when published.
- **Units**: Offer prices are CAD/MWh; block, available, and dispatched quantities are MW.
- **Source**: Authenticated AESO APIM Energy Merit Order Report.
- **Availability**: Reports are released with a historical delay; the tool enforces the published
  date window and returns a finality marker for the report response.
- **Interpretation**: An offer block is a submitted bid observation, not proof of dispatch,
  scarcity, participant conduct, or the cause of a Pool Price movement.
""",
    "unit-commitments": """# Methodology: Unit Commitments

- **Definition**: AESO generating-unit commitment directives, with issue, begin, operation-start,
  and operation-end timestamps where available.
- **Units**: This report is event-oriented and has no numeric measurement unit.
- **Source**: Authenticated AESO APIM Unit Commitment Data.
- **Intervals**: Requests are bounded to inclusive report dates and returned in deterministic
  pages; timestamps are normalized to `America/Edmonton`.
- **Interpretation**: A directive records an operating instruction or schedule publication. It is
  not a realized output measurement or evidence of price causation.
""",
    "generation-capacity": """# Methodology: Generation Capacity

- **Definition**: Hourly generation capability by fuel class, with maximum capability, operating
  and mothball outage components, and available capability where published.
- **Units**: All capability and outage fields are MW.
- **Source**: Authenticated AESO APIM AIES Generation Capacity and Outages report.
- **Forecast semantics**: Ranges that include current or future dates can be forecast or
  preliminary. Inspect `metadata.status`, observation type, finality, and warnings.
- **Interpretation**: Capability is not the same as dispatch or actual generation; missing fields
  are not zero and should remain missing in downstream analysis.
""",
    "load-outage-forecast": """# Methodology: Load Outage Forecast

- **Definition**: Hourly forecast observations for load and outage conditions used by AESO
  operational planning.
- **Units**: Forecast values are MW, with one interval start per local market hour.
- **Source**: Authenticated AESO APIM Load Outage Forecast report.
- **Forecast semantics**: This dataset is forecast/preliminary by definition. It may be revised;
  do not use it as an observed Alberta Internal Load or realized outage measurement.
- **Intervals**: Date ranges are bounded and normalized to `America/Edmonton`; preserve source
  metadata and warnings when comparing forecast values with actual load.
""",
    "intertie-capability": """# Methodology: Intertie Capability

- **Definition**: Hourly import/export capability and reliability-margin observations for AESO
  interties and flowgates, with optional report revisions.
- **Units**: Transfer capability, reliability margins, and gross offers are MW; hour-ending is a
  source report label, not a replacement for the explicit interval timestamp.
- **Source**: Authenticated AESO APIM Intertie Capability report.
- **Revision semantics**: `include_versions` can retain published revisions; `is_current`,
  `updated_at`, and `revision_updated_at` identify the source's current/revision state.
- **Interpretation**: Capability is a transfer limit or offer observation, not actual interchange
  flow and not proof that a constraint caused a price outcome.
""",
    "intertie-outages": """# Methodology: Intertie Outages

- **Definition**: Outage intervals affecting an intertie or flowgate, including the elements and
  paths reported as affected.
- **Units**: This report is event-oriented and has no numeric measurement unit.
- **Source**: Authenticated AESO APIM Intertie Outages report.
- **Intervals**: Each record has explicit `[interval_start, interval_end)` boundaries in
  `America/Edmonton`; the service bounds requested date ranges.
- **Interpretation**: A planned or reported outage is not an observed flow reduction or proof of
  a causal price effect. Use interval, metadata, and warning fields together.
""",
    "metered-volume": """# Methodology: Metered Volume

- **Definition**: Hourly metered energy by asset, with pool participant and asset-class fields
  where published.
- **Units**: Metered volume is MWh for each hourly interval.
- **Source**: Authenticated AESO APIM Metered Volumes report.
- **Filtering and bounds**: Requests can filter by asset IDs or pool-participant IDs, but not both;
  date ranges and page sizes are bounded to keep responses deterministic.
- **Interpretation**: Metered energy is a measurement for the reported asset and interval. It is
  not an offer, commitment, dispatch instruction, or a settlement assertion beyond the source
  report's metadata.
""",
    "operating-reserve-offer-control": """# Methodology: Operating Reserve Offer Control

- **Definition**: Historical operating-reserve offer-control blocks, including commodity/product,
  asset, volume, offer-control, and price fields where published.
- **Units**: Offer and activation prices are CAD/MWh; reserve volumes are MW.
- **Source**: Authenticated AESO APIM Operating Reserve Offer Control report.
- **Availability**: Reports are released with a historical delay; the tool enforces the published
  date window and returns bounded pages.
- **Interpretation**: An offer-control block is a submitted reserve-market observation, not proof
  of activation, physical delivery, participant conduct, or price causation.
""",
    "market-history": """# Methodology: Market History Summary

- **Definition**: Server-side aggregates of Pool Price and, optionally, Alberta Internal Load
  over hourly, daily, weekly, or monthly market-time buckets.
- **Units**: Pool Price statistics are CAD/MWh; load statistics are MW; observation counts are
  counts of source intervals included in each bucket.
- **Source**: Pool Price and load services backed by the configured authenticated AESO/GridStatus
  data path.
- **Bucket semantics**: Bucket boundaries are timezone-aware `America/Edmonton` intervals. DST
  transition days can contain 23 or 25 local hours; missing source intervals remain reflected in
  observation counts rather than being filled.
- **Interpretation**: Aggregates describe observed data and are not causal explanations or
  settlement-final statements unless the response metadata says so.
""",
    "supply-tightness": """# Methodology: Supply Tightness

- **Definition**: Deterministic screening arithmetic combining the latest market snapshot with
  available and maximum generation capability, outage components, interchange, load, and
  contingency-reserve requirement.
- **Units**: Inputs and margins are MW; the reserve-adjusted margin percentage is relative to
  Alberta Internal Load.
- **Source**: Current Supply Demand plus authenticated AESO APIM Generation Capacity and Outages.
- **Signal semantics**: `tight`, `watch`, and `comfortable` are transparent screening labels
  derived from returned arithmetic, not AESO declarations, forecasts, or market-power findings.
- **Caveat**: Missing inputs produce an `unknown` signal where required. The result describes
  supply conditions and does not establish why a price moved.
""",
    "historical-generation": """# Methodology: Historical CSD Generation

- **Definition**: Individual-asset operational generation, capability, fuel, and geography fields
  from AESO's Historical Current Supply Demand archive.
- **Resolution**: Hourly or five-minute average MW with explicit local and UTC interval bounds.
- **Timestamp semantics**: The published fixed `Date (MST)` value is converted to UTC and then to
  `America/Edmonton`, preserving repeated fall-back instants.
- **Storage**: The optional analytics extra enables an internal DuckDB index and partitioned
  Parquet snapshots. No arbitrary SQL tool is exposed.
- **Caveat**: AESO identifies CSD history as operational data. It is not settlement-metered
  generation and should not be described as final settlement output.
""",
    "research-analytics": """# Methodology: Research Analytics

- **Inputs**: Calculations consume complete internal price, load, CSD generation, outage,
  capability, forecast, or offer series rather than a user-visible page.
- **Capture price**: Generation-weighted hourly Pool Price; capture rate divides by arithmetic
  mean Pool Price over the requested window.
- **Forecast error**: Error is forecast minus actual; MAPE excludes zero actual observations.
- **Intertie metric**: Gross offer divided by available transfer capability is a utilization
  proxy, not metered interchange flow.
- **Reproducibility**: `analyze_market_event` returns an `analysis_manifest` containing the
  methodology version, normalized parameters, ordered input-source records, and degradation
  warnings. Its deterministic SHA-256 `analysis_id` changes when those inputs change.
- **Source identity**: Manifest records preserve publication and retrieval times, requested bounds,
  observation counts, finality, completeness, and available source identifiers or hashes. Missing
  optional evidence remains a warning rather than an observed zero.
- **Caveat**: Window changes, high-outage differences, and correlation are descriptive
  associations. They do not establish causation.
""",
    "operating-reserve-market": """# Methodology: Operating Reserve Market

- **Products**: Regulating, spinning, and supplemental reserves are published for active and
  standby procurement.
- **Price semantics**: Active price, standby premium, standby activation strike, and standby
  clearing blended price remain separate. The summary uses active price for active products and
  clearing blended price for standby products.
- **Forecast**: The seven-day volume report is forecast/preliminary, not realized procurement.
- **Activations**: Standby activations report volume and weighted-average activation price; summary
  activation prices are volume-weighted.
- **Caveat**: Offer-control blocks, procurement prices, forecast volumes, and activations are
  different report concepts and are not silently merged.
""",
    "official-research-data": """# Methodology: Official Research Data

- **Archive contract**: `get_research_data` selects one named official dataset and returns a
  bounded page. Historical adequacy/cushion web codes, transmission outages, planning-area
  load/generation, constrained volume, EEA events, OR directives, and the current Pool Participant
  registry retain their distinct fields, periods, finality, and units.
- **Missing values**: Blank planning-area numeric fields remain null. Missing years, participant
  mappings, price matches, and report families are unobserved, never zero.
- **Constraint units**: AESO defines the fixed CSV's `Constrained_MW` field as constrained volume
  for the entire hour in MWh; the typed output follows that published definition.
- **Participant mapping**: Concentration joins historical merit-order asset IDs to current asset
  and Pool Participant registries. It does not establish historical ownership, operator identity,
  corporate parent, market power, or causality; unmapped blocks remain explicit.
- **Frequency**: Raw rows are internal and input windows are limited to six elapsed hours.
  Threshold exposure counts ten-second intervals whose minimum/maximum crossed a threshold, so it
  is a proxy/upper bound rather than exact time outside the threshold. RoCoF is not inferred.
- **Association boundary**: Regional, constraint-price, and scarcity results are descriptive.
  They do not establish why a constraint, alert, or price outcome occurred.
""",
    "uc-settlement": """# Methodology: Unit Commitment Settlement Summary

- **Definition**: Hourly public Unit Commitment settlement amount and total charged volume.
- **Units**: Total UC amount is CAD; total charged volume is MW as labeled by the source report.
- **Source**: AESO ETS public Unit Commitment Settlement Summary, read without an APIM key.
- **Date semantics**: Requests use inclusive market report dates; returned rows have explicit
  `America/Edmonton` hourly intervals.
- **Distinction**: This settlement summary is separate from the authenticated Unit Commitment
  directive report and from actual unit generation.
""",
    "official-forecasts": """# Methodology: Official Forecasts

- **Series**: `get_forecast` uses one typed contract for AIL, Pool Price, wind, solar, and the
  current combined wind/solar publication. Historical renewable files are separate by fuel.
- **Cadence**: Current 12-hour renewable forecasts are ten-minute; current seven-day and
  historical actual-versus-forecast files are hourly. Pool Price and AIL are hourly.
- **Units**: Renewable and AIL values are MW; Pool Price is CAD/MWh.
- **Semantics**: Forecast, actual, minimum, maximum, capacity, issue time, and target time remain
  distinct. Missing actuals are not replaced with forecasts or zero.
- **Finality**: Current forecasts and recently posted actuals are preliminary/revisable. Yearly
  historical renewable files are marked final only according to their source publication.
""",
    "supply-adequacy": """# Methodology: Supply Adequacy and Supply Cushion

- **Source**: Named AESO Supply Adequacy and Market Supply Cushion status report.
- **Cadence**: Hourly status bands in the current seven-day publication, in
  `America/Edmonton`.
- **Fields**: Official adequacy and cushion codes/text are retained. Numeric MW values remain
  null when the publication exposes only a band; the service does not invent a band midpoint.
- **Distinction**: These AESO-published statuses are not the derived `tight`, `watch`, or
  `comfortable` screening label returned by `assess_supply_tightness`.
""",
    "supply-surplus": """# Methodology: Supply Surplus

- **Source**: Named AESO Supply Surplus status report and, for separate historical context,
  bounded AIES Event Log messages.
- **Semantics**: Status codes describe whether forecast prices include zero-price hours. They
  are forecasts/statuses, not a value inferred from observed Pool Price.
- **Event bounds**: A contiguous run gets an end only when a later published interval provides
  that boundary. Open runs keep `end=null`; no duration is manufactured.
- **Analytics**: Price, load, and renewable observations during an event are descriptive
  associations and do not establish why the surplus occurred.
""",
    "ffr-net-schedule": """# Methodology: FFR Net Schedule

- **Source**: Official AESO historical intertie-capability CSV used by the FFR Net Schedule
  publication.
- **Units and sign**: MW; AESO defines imports as negative and exports as positive.
- **Components**: BC and Montana schedules remain separate from the net value. This dataset is
  scheduled transfer, not FFR offered, dispatched, activated, or an operating-reserve product.
- **Cadence/finality**: Hourly historical observations, published daily for the prior day.
""",
    "dds-tmr-system-events": """# Methodology: DDS, TMR, and System Events

- **DDS**: `get_dispatch_down_service` returns published Dispatch Down Service availability in
  MW. DDS is distinct from energy dispatch, generation, and metered output.
- **TMR**: `get_tmr_reference_price` returns AESO's published CAD/MWh value by effective date;
  it is not independently recomputed.
- **System events**: `get_system_events` returns raw AIES Event Log comments plus a deterministic
  descriptive label. The raw comment remains authoritative and no event end is inferred.
- **Finality**: DDS can be preliminary; TMR publications are treated as final for their effective
  dates; event-log finality remains unknown unless AESO states otherwise.
""",
}


def register_methodology_resources(mcp: FastMCP) -> None:
    """Register dataset-specific methodology resources."""

    def register_one(uri: str, content: str) -> None:
        @mcp.resource(uri, mime_type="text/markdown")
        def methodology() -> str:
            return content

    for dataset, markdown in METHODOLOGY_MARKDOWN.items():
        register_one(f"aeso://methodology/{dataset}", markdown)
