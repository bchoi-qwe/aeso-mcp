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
| Approved Tx outages | `get_approved_transmission_outages` | ETS CSV via GridStatus | none* | Yes | GridStatus HTML→CSV | implemented |
| Long-range Tx outages | `get_long_range_transmission_outages` | Public report CSV | none | No | direct public-reports client | implemented |
| MCSINR | `get_monthly_cumulative_net_revenue` | ETS public CSV | none | No | direct | implemented |
| Secondary Offer Price Limit | `get_secondary_offer_price_limit` | ETS public CSV | none | No | direct | implemented |
| Energy Merit Order | — | APIM | key | TBD | Do not scrape | backlog |
| Metered volumes | — | APIM | key | TBD | Do not scrape | backlog |
| Unit commitment directives | — | APIM | key | TBD | Do not scrape | backlog |
| UC Summary settlement | — | ETS public | none | No | direct | backlog |
| Intertie ATC / TTC outages | — | APIM Intertie API first | key | TBD | Compare before scrape | backlog |

\*GridStatus fetches ETS public HTML/CSV without sending the APIM key to `ets.aeso.ca`. The MCP still requires `AESO_API_KEY` for the overall server because other tools use APIM.

## Semantic notes

- **Approved transmission outages** are AESO-approved planned outages (`approval_status=approved`).
- **Long Range Significant Transmission Outages** are forward-looking and may be tentative (`approval_status=tentative`). Do not merge them silently with approved outages.
- Generator outages (`get_outages`) are a different concept from transmission outages.
