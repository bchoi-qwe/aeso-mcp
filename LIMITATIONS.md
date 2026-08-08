# Known limitations

Honest inventory of gaps and caveats for `aeso-mcp` **v0.1.x**.
This project is intentionally **not published to PyPI** until these items have been
human-reviewed and accepted (or fixed).

## Distribution

- Install from GitHub (`uvx --from git+…` or clone + `uv run`). PyPI is deferred.
- MCP Registry listing is not published yet (`server.json` is a placeholder without a PyPI package entry until publication).
- FastMCP is pinned to a **prerelease** (`4.0.0b2`) to target MCP protocol generation
  `2026-07-28`; expect framework churn.

## Data coverage

| Area | Reality |
| --- | --- |
| Historical generation | **Wind and solar only** (public AESO/GridStatus coverage). Current fuel mix is all fuels. |
| Direct APIM provider | Used for contract tests and some parsing paths. Outages and historical generation **raise** `UnsupportedDatasetError` on the direct APIM adapter. Runtime uses GridStatus. |
| Outages | Generator outages via GridStatus as **hourly aggregated capacity by fuel/technology**. Approved and long-range transmission outages via credential-free public-reports client (`approval_status=approved` / `tentative`). |
| Merit order / unit commitments / metered volumes | Not implemented yet — prefer **APIM**, do not scrape ETS copies. |
| Market-power public reports (MCSINR, secondary offer limit) | Implemented as current ETS CSV publications via credential-free public-reports client. Historical windows not yet supported. |
| Settlement finality | Operational feeds may be preliminary; metadata does not claim final settlement. |

## Analytics

- `explain_market_conditions` returns **associated changes**, not causal claims (warnings say so).
- `compare_forecast_to_actual` covers Alberta Internal Load forecast vs actual only.
- Analytics that cannot load price/load history fail or return partial stats rather than fabricating values.

## Operations / CI

- Live AESO integration tests are **opt-in** (`tests/integration`, needs a real `AESO_API_KEY`).
  The suite now smokes snapshot, load, SMP, interchange, reserves, assets, and outages in
  addition to pool price / fuel mix.
- CI does not call live AESO (uses a dummy key for unit/contract/MCP tests).
- Official MCP conformance is exercised in CI with an **application-server baseline**; many
  everything-server scenarios are intentionally unsupported.
- In-process TTL cache is bounded (`AESO_MCP_CACHE_MAX_ENTRIES`, default 512).

## Security / trust

- Requires `AESO_API_KEY`; never log or return the key.
- No arbitrary URL fetch, shell, or SQL tools.
- Rotate any API key that was ever pasted into chat, tickets, or logs.
