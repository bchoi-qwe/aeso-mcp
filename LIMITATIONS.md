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
| Historical generation | **Wind and solar only** (public AESO/GridStatus coverage). Current fuel mix is all fuels. |
| Direct APIM provider | Production operational-report tools use the authenticated client. Historical wind/solar and the core price/load adapters continue to prefer GridStatus where it already implements AESO. |
| Outages | Generator outages are hourly aggregated capacity by fuel/technology. AIES capability, load-outage forecasts, and intertie capability outages are separate APIM tools. Approved and long-range transmission outages retain distinct `approved` / `tentative` status. |
| Merit order / unit commitments / metered volumes | Implemented through authenticated APIM with source-specific publication limits, query bounds, and output pagination. |
| Market-power public reports (MCSINR, secondary offer limit) | Implemented as current ETS CSV publications through an internal no-key upstream client. The complete server still always requires `AESO_API_KEY`; there is no public-reports-only mode. Historical windows are not yet supported. |
| Settlement finality | Operational feeds may be preliminary; metadata does not claim final settlement. |

## Analytics

- `explain_market_conditions` returns **associated changes**, not causal claims (warnings say so).
- `compare_forecast_to_actual` covers Alberta Internal Load forecast vs actual only.
- `assess_supply_tightness` is transparent screening arithmetic, not an AESO declaration or a
  causal price model. Its `tight` / `watch` thresholds are documented in every response.
- Analytics that cannot load price/load history fail or return partial stats rather than fabricating values.

## Operations / CI

- Live AESO integration tests require a real `AESO_API_KEY`; they are opt-in locally and run in a
  scheduled protected GitHub environment when that secret is configured.
  The suite now smokes snapshot, load, SMP, interchange, reserves, assets, and outages in
  addition to pool price / fuel mix.
- CI does not call live AESO (uses a dummy key for unit/contract/MCP tests).
- Official MCP conformance is exercised in CI with an **application-server baseline**; many
  everything-server scenarios are intentionally unsupported.
- In-process TTL cache is bounded (`AESO_MCP_CACHE_MAX_ENTRIES`, default 512).
- HTTP rate limits, concurrency limits, and cache state are per process; horizontally scaled
  deployments need external coordination if they require global limits or shared cache state.

## Security / trust

- Requires `AESO_API_KEY`; never log or return the key.
- No arbitrary URL fetch, shell, or SQL tools.
- HTTP bearer authentication is optional and should be configured for remotely reachable servers.
- Rotate any API key that was ever pasted into chat, tickets, or logs.
