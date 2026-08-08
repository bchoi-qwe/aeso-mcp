# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Generator outages model matches GridStatus aggregated hourly capacity by fuel/technology
- MCSINR accounting negatives like `(46931.24)` parse as negative floats
- AESO hour-ending parser supports `HE 02*` / DST fold semantics; range validation compares in UTC
- Public-report schema drift raises `DataValidationError` instead of silent empty results
- Public-report HTTP client re-validates redirect targets against the host allow-list
- Cache single-flight cancels waiters when the owner task is cancelled
- Market snapshot CSD fetch uses APIM HTTP instead of GridStatus private `_make_request`
- Approved transmission outages use the timeout-controlled public-reports client
- Dependency canary rewrites the exact FastMCP pin so upgrades are actually tested
- MCP conformance job is blocking; `server.json` no longer claims a published PyPI package

### Added

- `docs/data-sources.md` coverage matrix (APIM-first resolution order)
- Approved transmission outages (`get_approved_transmission_outages`) via public-reports client
- Credential-free public-reports HTTP client and Long Range Significant Transmission Outages
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

- PyPI publish workflow is **manual only** (`workflow_dispatch`); no auto-publish on GitHub releases
- Direct APIM adapter raises `UnsupportedDatasetError` for outages and historical generation instead of returning empty lists
- GridStatus renewable/history and optional load-forecast paths no longer swallow authentication failures
- Snapshot / analytics optional enrichment no longer swallows authentication failures
- Unsupported request flags locked to `Literal[True]` until historical support exists

## [0.1.1] - 2026-08-07

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

[Unreleased]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/bchoi-qwe/aeso-mcp/releases/tag/v0.1.0
