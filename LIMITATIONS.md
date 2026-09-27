# Known limitations

Honest inventory of gaps and caveats for the current `aeso-mcp` release.

## Distribution

- PyPI is the supported package source (`uvx aeso-mcp`); direct GitHub installation remains
  available for users who intentionally want the current repository version.
- `server.json` declares the same PyPI artifact for MCP Registry discovery; the Registry stores
  server metadata rather than hosting package artifacts.
- FastMCP 4 is stable and pinned to the tested `4.0.10` patch. The server targets MCP protocol
  generation `2026-07-28`; FastMCP 4 negotiates the modern protocol with compatible clients while
  retaining support for legacy session-based clients. The FastMCP 4 line is still young, so
  framework behavior remains covered by locked deterministic tests, conformance, and a non-blocking
  4.0.x dependency canary.

## Data coverage

| Area | Reality |
| --- | --- |
| Historical generation | `get_historical_generation` covers individual assets and all fuel labels published in AESO's hourly/five-minute CSD archive. This is operational average MW, not settlement-metered output. Legacy `get_generation` history remains wind/solar for compatibility. |
| Direct APIM provider | Production operational-report tools use the authenticated client. Historical wind/solar and the core price/load adapters continue to prefer GridStatus where it already implements AESO. |
| Outages | Generator outages are hourly aggregated capacity by fuel/technology. AIES capability, load-outage forecasts, and intertie capability outages are separate APIM tools. Approved and long-range transmission outages retain distinct `approved` / `tentative` status. |
| Merit order / unit commitments / metered volumes | Implemented through authenticated APIM with source-specific publication limits, query bounds, and output pagination. |
| Forecasts | AIL, Pool Price, wind, solar, and current combined wind/solar are implemented. AESO publishes historical wind and solar separately; combined historical mode is therefore unsupported. Current products are revisable and may not yet contain actuals. Point-in-time `as_of` results require known publication chronology and retrieval no later than the boundary. Retrieval snapshots preserve the actual/finality state observed at that time; unavailable earlier publications remain unobserved. |
| Supply adequacy / surplus | Current named reports remain official categorical status bands. Fixed historical data-request files now expose adequacy/cushion web codes, but not a fabricated numeric MW cushion. The 24-month forecast and any longer series not present in those verified files remain unsupported. |
| Spatial / constraint history | Planning-area hourly load/generation and constrained MWh/minutes are available from fixed official 2015–2025 and 2020–2025 files respectively. AESO states no further constrained-volume updates are planned. Associations with Pool Price or outages are descriptive, not causal congestion findings. |
| Participant mapping | The Pool Participant API and current asset registry support mapping-based offered-volume concentration. This is not a historical ownership, operator, or corporate-parent series; unmapped merit-order blocks are reported and excluded from mapped shares. |
| Reliability history | Fixed EEA and aggregated OR-directive archives are exposed only for their published periods. Missing years or report families are unavailable, not zero. |
| System frequency | MCP exposes compact statistics for a maximum six-hour window from verified 10-second yearly assets. It does not return raw rows, compute RoCoF, or claim exact time outside thresholds from interval minima/maxima. The first fetch may read a large official yearly asset. |
| FFR / DDS / TMR / events | FFR Net Schedule, DDS availability, TMR reference price, and AIES Event Log are implemented. FFR schedule is not offer/dispatch/activation; DDS exposes only verified availability fields; event endings are never inferred. |
| Market-power public reports (MCSINR, secondary offer limit) | Current ETS CSV publications are implemented. AESO documentation confirms historical material exists, but no stable bounded machine-readable archive endpoint and schema was verified, so historical windows remain intentionally unsupported rather than inferred or scraped. |
| Historical store | Optional `aeso-mcp[analytics]` dependencies enable local DuckDB/Parquet persistence and safe complete, non-preliminary reads for Pool Price and AIL while preserving the source finality marker. Schema v3 adds forecast vintages, retrieval snapshots, and additive v1/v2 migration. Existing v3 stores retain their last observed state; overwritten earlier states cannot be reconstructed. Legacy rows with unknown chronology remain available without `as_of` but are excluded from point-in-time results. The store is single-process/local and is not a shared warehouse. |
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
- `analyze_market_event` includes a reproducibility manifest, but that identity describes the
  returned inputs and parameters; it does not make a revisable upstream publication immutable.
- Participant concentration is computed only over blocks mapped through the current registries.
  System-frequency threshold exposure is a flagged-interval proxy/upper bound rather than exact
  duration outside the thresholds.

## Operations / CI

- Live AESO integration tests require a real `AESO_API_KEY`; they are opt-in locally and run in a
  scheduled protected GitHub environment when that secret is configured.
  The suite now smokes core/APIM data, the CSD archive, wind/solar/combined and Pool Price
  forecasts, and the named adequacy, surplus, FFR, DDS, TMR, and system-event reports.
- CI does not call live AESO (uses a dummy key for unit/contract/MCP tests).
- Official MCP conformance is exercised in CI with an **application-server baseline**; many
  everything-server scenarios are intentionally unsupported.
- CI rejects any prerelease package in the locked dependency graph. FastMCP is pinned to the
  tested stable 4.0.10 release; the scheduled non-blocking canary probes only compatible 4.0.x
  patches.
- In-process TTL cache is bounded (`AESO_MCP_CACHE_MAX_ENTRIES`, default 512).
- HTTP rate limits, concurrency limits, and cache state are per process; horizontally scaled
  deployments need external coordination if they require global limits or shared cache state.

## Security / trust

- Requires `AESO_API_KEY`; never log or return the key.
- Public-report and fixed-asset clients are credential-free and allow-listed; response streams have
  25 MiB and 384 MiB byte caps respectively before full-response assembly.
- No arbitrary URL fetch, shell, or SQL tools.
- Non-loopback HTTP binding requires bearer authentication by default. The explicit insecure
  override is intended only for isolated deployments that knowingly accept the exposure.
- Rotate any API key that was ever pasted into chat, tickets, or logs.
