# Known limitations

Honest inventory of gaps and caveats for the current `aeso-mcp` release.

## Distribution

- PyPI is the supported package source (`uvx aeso-mcp`); direct GitHub installation remains
  available for users who intentionally want the current repository version.
- `server.json` declares the same PyPI artifact for MCP Registry discovery; the Registry stores
  server metadata rather than hosting package artifacts.
- The server currently uses a **prerelease** FastMCP 4 dependency to target MCP protocol
  generation `2026-07-28`; expect framework churn.

## Data coverage

| Area | Reality |
| --- | --- |
| Historical generation | `get_historical_generation` covers individual assets and all fuel labels published in AESO's hourly/five-minute CSD archive. This is operational average MW, not settlement-metered output. Legacy `get_generation` history remains wind/solar for compatibility. |
| Direct APIM provider | Production operational-report tools use the authenticated client. Historical wind/solar and the core price/load adapters continue to prefer GridStatus where it already implements AESO. |
| Outages | Generator outages are hourly aggregated capacity by fuel/technology. AIES capability, load-outage forecasts, and intertie capability outages are separate APIM tools. Approved and long-range transmission outages retain distinct `approved` / `tentative` status. |
| Merit order / unit commitments / metered volumes | Implemented through authenticated APIM with source-specific publication limits, query bounds, and output pagination. |
| Forecasts | AIL, Pool Price, wind, solar, and current combined wind/solar are implemented. AESO publishes historical wind and solar separately; combined historical mode is therefore unsupported. Current forecast products are revisable and may not yet contain actuals. |
| Supply adequacy / surplus | Current named reports are implemented as official categorical status bands. The 24-month forecast, long-term adequacy series, and data-request historical files are not exposed because a stable, bounded machine-readable locator/schema was not verified in this cycle. |
| FFR / DDS / TMR / events | FFR Net Schedule, DDS availability, TMR reference price, and AIES Event Log are implemented. FFR schedule is not offer/dispatch/activation; DDS exposes only verified availability fields; event endings are never inferred. |
| Market-power public reports (MCSINR, secondary offer limit) | Current ETS CSV publications are implemented. AESO documentation confirms historical material exists, but no stable bounded machine-readable archive endpoint and schema was verified, so historical windows remain intentionally unsupported rather than inferred or scraped. |
| Historical store | Optional `aeso-mcp[analytics]` dependencies enable local DuckDB/Parquet persistence and safe complete, non-preliminary reads for Pool Price and AIL while preserving the source finality marker. Incomplete or preliminary coverage falls back live. The store is single-process/local and is not a shared warehouse. |
| Operating reserves | Public active/standby prices, seven-day volume forecasts, and standby activations are implemented. The offer-control APIM report remains a distinct delayed dataset. |
| Settlement finality | Operational feeds may be preliminary; metadata does not claim final settlement. |

## Analytics

- `explain_market_conditions` returns **associated changes**, not causal claims (warnings say so).
- Forecast retrieval covers AIL, Pool Price, wind, solar, and current combined wind/solar.
  Forecast-error analytics require paired actuals; current/future rows without actuals remain
  excluded and counted, not replaced with zero.
- `assess_supply_tightness` is transparent screening arithmetic, not an AESO declaration or a
  causal price model. Its `tight` / `watch` thresholds are documented in every response.
- Analytics that cannot load price/load history fail or return partial stats rather than fabricating values.
- Event, outage, and intertie analyses report descriptive associations or proxies, not causal
  attribution or metered intertie utilization.

## Operations / CI

- Live AESO integration tests require a real `AESO_API_KEY`; they are opt-in locally and run in a
  scheduled protected GitHub environment when that secret is configured.
  The suite now smokes core/APIM data, the CSD archive, wind/solar/combined and Pool Price
  forecasts, and the named adequacy, surplus, FFR, DDS, TMR, and system-event reports.
- CI does not call live AESO (uses a dummy key for unit/contract/MCP tests).
- Official MCP conformance is exercised in CI with an **application-server baseline**; many
  everything-server scenarios are intentionally unsupported.
- In-process TTL cache is bounded (`AESO_MCP_CACHE_MAX_ENTRIES`, default 512).
- HTTP rate limits, concurrency limits, and cache state are per process; horizontally scaled
  deployments need external coordination if they require global limits or shared cache state.

## Security / trust

- Requires `AESO_API_KEY`; never log or return the key.
- No arbitrary URL fetch, shell, or SQL tools.
- Non-loopback HTTP binding requires bearer authentication by default. The explicit insecure
  override is intended only for isolated deployments that knowingly accept the exposure.
- Rotate any API key that was ever pasted into chat, tickets, or logs.
