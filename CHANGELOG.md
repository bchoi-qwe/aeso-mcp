# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `compare_forecast_to_actual` analytics tool (AIL forecast error metrics)

### Fixed

- Market snapshot now fetches Current Supply Demand once (no redundant CSD calls)
- Snapshot AIL extraction from raw CSD `alberta_internal_load`
- Cache TTLs now use short TTL for ranges overlapping the current market day
- Snapshot pool price / SMP selection uses latest `interval_start`

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

[Unreleased]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bchoi-qwe/aeso-mcp/releases/tag/v0.1.0
