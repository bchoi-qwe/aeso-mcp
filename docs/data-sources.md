# Data sources

Canonical coverage matrix for AESO datasets used by `aeso-mcp`.

The [generated dataset and retrieval coverage](generated/mcp-catalog.md#dataset-and-retrieval-coverage)
is rebuilt from the live MCP registration surface in CI. The source matrix below adds the
human-reviewed provider priority and authentication boundary.

## Resolution order

1. Official AESO APIM (authenticated)
2. GridStatus implementation of an official source
3. AESO machine-readable public report (CSV via allow-listed hosts, no credentials)
4. HTML parsing only as a last resort for a named report

Never scrape ETS copies of datasets that already exist on APIM.

## Coverage

| Dataset | MCP tool | Canonical source | Auth | GridStatus | Public fallback | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Market snapshot | `get_market_snapshot` | APIM CSD + prices | key | Yes | None | implemented |
| Pool Price | `get_pool_prices` | APIM | key | Yes | None | implemented |
| System Marginal Price | `get_system_marginal_prices` | APIM | key | Yes | None | implemented |
| Alberta Internal Load | `get_load` | APIM | key | Yes | None | implemented |
| Fuel mix / generation | `get_generation` | APIM CSD; wind/solar history | key | Yes | None | implemented |
| Interchange | `get_interchange` | APIM CSD | key | Yes | None | implemented |
| Operating reserves | `get_reserves` | APIM CSD | key | Yes | None | implemented |
| Generator outages | `get_outages` | APIM / GridStatus | key | Yes | None | implemented |
| Assets | `get_assets` | APIM | key | Yes | None | implemented |
| Approved Tx outages | `get_approved_transmission_outages` | ETS CSV via public-reports client | none* | No | HTML→CSV with timeouts | implemented |
| Long-range Tx outages | `get_long_range_transmission_outages` | Public report CSV | none | No | direct public-reports client | implemented |
| MCSINR | `get_monthly_cumulative_net_revenue` | ETS public CSV | none | No | direct | implemented |
| Secondary Offer Price Limit | `get_secondary_offer_price_limit` | ETS public CSV | none | No | direct | implemented |
| AIES generation capacity/outages | `get_generation_capacity` | APIM | key | Yes for hourly outages | None | implemented |
| Load outage forecast | `get_load_outage_forecast` | APIM | key | No | None | implemented |
| Energy Merit Order | `get_energy_merit_order` | APIM | key | No | Do not scrape | implemented |
| Metered volumes | `get_metered_volumes` | APIM | key | No | Do not scrape | implemented |
| Unit commitment directives | `get_unit_commitments` | APIM | key | No | Do not scrape | implemented |
| UC Summary settlement | `get_uc_settlement_summary` | ETS public CSV | none | No | direct | implemented |
| Intertie ATC / TTC | `get_intertie_capability` | APIM Intertie API | key | No | None | implemented |
| Intertie capability outages | `get_intertie_outages` | APIM Intertie API | key | No | None | implemented |
| Operating Reserve Offer Control | `get_operating_reserve_offer_control` | APIM | key | No | Do not scrape | implemented |
| Historical individual-asset CSD generation | `get_historical_generation` | Official AESO Box archive | none | No | fixed shared-folder adapter | implemented |
| AIL actual / forecast | `get_forecast(series="ail")` | APIM Actual Forecast API | key | load merge | None | implemented |
| Pool Price forecast / actual | `get_forecast(series="pool_price")` | ETS Forecast and Actual Pool Price CSV | none | No | named public report | implemented |
| Wind / solar forecast | `get_forecast(series="wind"|"solar"|"wind_solar")` | [AESO wind and solar forecasting](https://www.aeso.ca/grid/grid-planning/forecasting/wind-and-solar-power-forecasting/) | none | No | fixed current CSVs; yearly aeso.ca links | implemented |
| Historical adequacy / cushion web codes | `get_research_data(dataset="supply_adequacy"|"supply_cushion")` | [AESO historical web-code files](https://www.aeso.ca/market/market-and-system-reporting/data-requests/historical-supply-adequacy-and-supply-cushion-web-codes/) | none | No | fixed XLSX assets | implemented |
| Historical transmission outages | `get_research_data(dataset="transmission_outages")` | [AESO historical transmission-outage data](https://www.aeso.ca/market/market-and-system-reporting/data-requests/historical-transmission-outages-data/) | none | No | fixed XLSX asset | implemented |
| Planning-area load/generation | `get_research_data(dataset="planning_area")` | [AESO planning-area hourly data](https://www.aeso.ca/market/market-and-system-reporting/data-requests/planning-area-hourly-load-and-generation/) | none | No | fixed yearly ZIP/CSV assets | implemented |
| Constrained volume by area/fuel | `get_research_data(dataset="constrained_volume")` | [AESO constrained-volume data](https://www.aeso.ca/market/market-and-system-reporting/data-requests/constrained-volume-by-planning-area-and-fuel-type/) | none | No | fixed CSV asset | implemented |
| Historical EEA / Grid Alerts | `get_research_data(dataset="eea_events")` | [AESO data requests](https://www.aeso.ca/market/market-and-system-reporting/data-requests/) | none | No | fixed XLSX asset | implemented |
| Historical OR directives | `get_research_data(dataset="or_directives")` | [AESO data requests](https://www.aeso.ca/market/market-and-system-reporting/data-requests/) | none | No | fixed XLSX assets | implemented |
| Pool Participant registry | `get_research_data(dataset="pool_participants")` | APIM Pool Participant API | key | No | Do not scrape | implemented |
| System frequency | `analyze_system_frequency` | [AESO 10-second frequency archive](https://www.aeso.ca/market/market-and-system-reporting/data-requests/) | none | No | fixed yearly CSV/ZIP assets; compact analysis only | implemented |
| Supply adequacy / market supply cushion | `get_supply_adequacy` | ETS Supply Adequacy named report | none | No | narrow HTML status-grid parser | implemented |
| Supply surplus | `get_supply_surplus` | ETS Supply Surplus named report | none | No | narrow HTML status parser | implemented |
| FFR Net Schedule | `get_ffr_net_schedule` | ETS historical intertie-capability CSV | none | No | fixed public CSV | implemented |
| Dispatch Down Service | `get_dispatch_down_service` | ETS DDS Market Report CSV | none | No | named date-bounded report | implemented |
| TMR reference price | `get_tmr_reference_price` | ETS TMR Reference Price CSV | none | No | fixed named report | implemented |
| AIES system events | `get_system_events` | ETS AIES Event Log CSV | none | No | named date-bounded report | implemented |
| Operating Reserve active/standby prices | `get_operating_reserve_prices` | ETS public CSV | none | No | direct | implemented |
| Operating Reserve volume forecast | `get_operating_reserve_forecast` | ETS public CSV | none | No | direct | implemented |
| Operating Reserve activations | `get_operating_reserve_activations` | ETS public CSV | none | No | direct | implemented |

\*Public-report tools contact `ets.aeso.ca` without sending the APIM key. This is an internal
source-specific client, not a separate runtime mode: the complete MCP server always requires
`AESO_API_KEY` and registers one unified tool surface.

## Semantic notes

- **Approved transmission outages** are AESO-approved planned outages (`approval_status=approved`).
- **Long Range Significant Transmission Outages** are forward-looking and may be tentative (`approval_status=tentative`). Do not merge them silently with approved outages.
- Generator outages (`get_outages`) are a different concept from transmission outages.
- Historical CSD generation is operational average MW, not settlement-metered generation.
- Current wind/solar 12-hour forecasts have ten-minute cadence; seven-day and historical
  actual-versus-forecast files are hourly. Historical wind and solar are separate files, so the
  combined series has no historical mode. `as_of` uses only persisted or retrieved vintages with
  known issue/publication chronology; unknown chronology is excluded.
- Supply-adequacy and supply-cushion publications expose official categorical bands. Null numeric
  MW fields mean the source did not publish a numeric value; the service never substitutes a band
  midpoint.
- FFR Net Schedule imports are negative and exports positive. It is scheduled intertie transfer,
  not FFR offered, dispatched, or activated.
- DDS availability, energy dispatch, and metered generation are different concepts. The current
  DDS contract exposes only fields verified in the named report.
- System-event labels are deterministic classifications of raw comments; comments remain
  authoritative and the service does not infer an end timestamp.
- Active reserve price, standby premium, activation strike, and clearing blended price are
  separate economic fields and must not be collapsed into one generic reserve price.
- Historical adequacy/cushion values are categorical web codes, not numeric MW. Planning-area
  blank numeric cells remain null. AESO defines the constrained archive's `Constrained_MW` column
  as constrained volume for the entire hour in MWh; the typed contract follows that published
  semantic rather than the header spelling alone.
- Pool Participant is a current registry. Participant, agent, asset operator, owner, and corporate
  parent are not interchangeable; concentration analysis reports its current-mapping basis and
  unmapped historical blocks.
- Raw system-frequency observations are not exposed through MCP. Threshold exposure is a count of
  flagged ten-second intervals expressed as an upper-bound proxy, not exact duration outside a
  threshold.
