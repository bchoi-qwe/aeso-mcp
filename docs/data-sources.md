# Data sources

Canonical coverage matrix for AESO datasets used by `aeso-mcp`.

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
| UC Summary settlement | — | ETS public | none | No | direct | backlog |
| Intertie ATC / TTC | `get_intertie_capability` | APIM Intertie API | key | No | None | implemented |
| Intertie capability outages | `get_intertie_outages` | APIM Intertie API | key | No | None | implemented |
| Operating Reserve Offer Control | `get_operating_reserve_offer_control` | APIM | key | No | Do not scrape | implemented |

\*Public-report tools contact `ets.aeso.ca` without sending the APIM key. This is an internal
source-specific client, not a separate runtime mode: the complete MCP server always requires
`AESO_API_KEY` and registers one unified tool surface.

## Semantic notes

- **Approved transmission outages** are AESO-approved planned outages (`approval_status=approved`).
- **Long Range Significant Transmission Outages** are forward-looking and may be tentative (`approval_status=tentative`). Do not merge them silently with approved outages.
- Generator outages (`get_outages`) are a different concept from transmission outages.
