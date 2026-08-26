# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Official individual-asset Historical CSD Generation Data at hourly and five-minute resolution,
  with fixed-MST-to-UTC DST-safe normalization and source provenance
- Optional incremental DuckDB index, source manifests, and partitioned Parquet snapshots for CSD
  generation, Pool Price, and AIL actual/forecast observations
- General forecast retrieval (`ail` currently supported) and public hourly UC settlement summary
- Deterministic price distribution, duration curve, market-event, capture-price, net-load,
  supply-stack, intertie, generation, dispatch, outage-association, and forecast-error analytics
- Public active/standby operating-reserve prices, seven-day volume forecast, standby activations,
  and product-level summaries with distinct price concepts
- Fifty-case canonical agent-use evaluation catalog with arguments, datasets, caveats, prohibited
  interpretations and numerical relationships; an external-result scorer; a generated MCP
  catalog; and a strict MkDocs documentation website with client configs, investigations, and
  methodology

### Changed

- MCP surface expanded from 27 to 47 tools and from 20 to 24 resources
- CI now verifies optional historical storage, the evaluation catalog, generated tool catalog,
  and strict documentation builds

## [0.2.0] - 2026-08-23

### Fixed

- Cache waiter cancellation no longer cancels shared single-flight work for other callers
- High-price event detection splits runs at missing intervals; partial explicit baselines are rejected
- Synthesized interval ends use elapsed UTC time across DST transitions
- AESO spring `HE 03` bridges the clock change; starred `HE 02*` is limited to fall-back days
- Current and future ranges use short cache TTLs instead of being treated as immutable history
- Authenticated APIM base URLs and redirects require HTTPS; invalid configuration values are not echoed
- The packaged glossary resource is Markdown rather than Python source; typed wheels include `py.typed`
- Generator outages model matches GridStatus aggregated hourly capacity by fuel/technology
- MCSINR accounting negatives like `(46931.24)` parse as negative floats
- AESO hour-ending parser supports `HE 02*` / DST fold semantics; range validation compares in UTC
- Public-report schema drift raises `DataValidationError` instead of silent empty results
- Public-report HTTP client re-validates redirect targets against the host allow-list
- Cache single-flight cancels waiters when the owner task is cancelled
- Market snapshot CSD fetch uses APIM HTTP instead of GridStatus private `_make_request`
- Approved transmission outages use the timeout-controlled public-reports client
- Dependency canary rewrites the exact FastMCP pin so upgrades are actually tested
- MCP conformance job is blocking; release metadata validation keeps package and Registry
  versions aligned
- Historical approved transmission publication windows use half-open `[start, end)` matching
  other date-range tools; live tests always close HTTP clients
- Half-open range filters and analytics durations use UTC so fall-back fold ambiguity is safe
- Authenticated APIM client/base URL are restricted to `apimgw.aeso.ca` (no wildcard `*.aeso.ca`)
- Direct APIM CSD helpers reuse shared `parse_csd_payload` (no capability-as-generation fallback)

### Added

- Authenticated operational APIM tools for Energy Merit Order, unit commitments, AIES generation
  capacity/outages, load-outage forecasts, intertie capability/outages, metered volumes, and
  Operating Reserve Offer Control
- `summarize_market_history` compact aggregation and `assess_supply_tightness` transparent
  reserve-adjusted supply-margin indicators
- Offset/limit pagination for raw market and operational series
- Fetch/serve/cache timing, completeness, available/missing series, and separate observation-type
  and finality metadata
- Stable machine-readable MCP error envelopes with upstream retry timing
- Hardened HTTP runtime controls: Host/Origin validation, optional bearer auth, rate/concurrency
  limits, request-size bounds, secret-free probes, and correlation IDs
- Reusable daily brief, price-event investigation, and market-day comparison prompts; expanded
  capabilities and methodology resources
- Installed-wheel and Docker runtime smoke checks, release-version consistency validation, and a
  scheduled protected live AESO canary
- `docs/data-sources.md` coverage matrix (APIM-first resolution order)
- Approved transmission outages (`get_approved_transmission_outages`) via public-reports client
- Isolated public-reports HTTP client and Long Range Significant Transmission Outages
  (`get_long_range_transmission_outages`, `approval_status=tentative`)
- Market-power public reports: `get_monthly_cumulative_net_revenue` (MCSINR) and
  `get_secondary_offer_price_limit`
- `TransmissionService` / `MarketPowerService` with separate public-report providers
- `LIMITATIONS.md` honest gap inventory; Pre-PyPI checklist in CONTRIBUTING
- Historical generation responses now use the semantic TTL cache
- Bounded in-memory cache (`AESO_MCP_CACHE_MAX_ENTRIES`, default 512)
- Expanded live integration smoke (snapshot, load, SMP, interchange, reserves, assets, outages)
- Snapshot marks `preliminary` when pool price or AIL is missing

### Changed

- The project remains one complete, API-key-required server package; public-report access is an
  internal source adapter and is not exposed as a credential-free runtime mode
- Long raw series default to bounded pages; server-side analytics explicitly operate on complete
  cached series rather than a returned page
- PyPI publish workflow is **manual only** (`workflow_dispatch`) and uses OIDC Trusted
  Publishing; no auto-publish on GitHub releases
- Direct APIM adapter raises `UnsupportedDatasetError` for outages and historical generation instead of returning empty lists
- GridStatus renewable/history and optional load-forecast paths no longer swallow authentication failures
- Snapshot / analytics optional enrichment no longer swallows authentication failures
- Unsupported request flags locked to `Literal[True]` until historical support exists

## [0.1.1] - 2026-08-07

### Added

- `compare_forecast_to_actual` analytics tool (AIL forecast error metrics)
- GitHub release packaging notes and publish workflow for PyPI

### Fixed

- Market snapshot now fetches Current Supply Demand once (no redundant CSD calls)
- Snapshot AIL extraction from raw CSD `alberta_internal_load`
- Cache TTLs now use short TTL for ranges overlapping the current market day
- Snapshot pool price / SMP selection uses latest `interval_start`
- Config unit test no longer false-passes when a local `.env` is present
- MCP conformance baseline format updated for application-server expected failures
- CI uv cache contention on the conformance job

## [0.1.0] - 2026-08-07

### Added

- Initial AESO MCP server targeting MCP protocol generation `2026-07-28` via FastMCP 4.x
- Core tools: `get_market_snapshot`, `get_pool_prices`, `get_system_marginal_prices`,
  `get_load`, `get_generation`, `get_interchange`, `get_reserves`, `get_outages`, `get_assets`
- Analytics tools: `compare_market_periods`, `find_price_events`, `explain_market_conditions`
- Resources: `aeso://glossary`, `aeso://datasets`, methodology notes for Pool Price and SMP
- GridStatus-backed provider with direct AESO APIM httpx client for contracts/gaps
- Query bounds, TTL cache with single-flight, retries, and domain error mapping
- Unit, contract, MCP, and optional live integration tests
- GitHub Actions CI, Dockerfile, Renovate config, and MCP registry `server.json`

[Unreleased]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/bchoi-qwe/aeso-mcp/releases/tag/v0.1.0
