# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-09-26

### Added

- Independent MCP conformance gates for legacy `2025-11-25` and frozen modern `2026-07-28`
  requirements, plus pinned Inspector CLI HTTP discovery and schema-portability smoke checks
- MCP interoperability/security guide documenting optional feature scope, HTTP auth, TLS
  termination, Registry metadata validation, and scenario-specific conformance baselines

- Immutable forecast-vintage storage with additive schema-v3 migration, source version/hash,
  issue/publication/retrieval time, stable deduplication, DST-safe target identity, and bounded
  `as_of` selection through the existing `get_forecast` abstraction
- One typed official research-archive contract for historical adequacy/cushion web codes,
  transmission outages, planning-area load/generation, constrained volume, EEA events,
  operating-reserve directives, and the current Pool Participant registry
- Participant concentration, regional load/generation, constrained-volume, scarcity-context, and
  compact six-hour system-frequency analyses; raw high-frequency rows remain server-side
- Deterministic `analysis_manifest` output for multi-source market-event research, including exact
  inputs, methodology version, normalized parameters, warnings, and a SHA-256 analysis identity
- Exact lockfile prerelease-policy checks, substantive low-covered provider/service tests, and an
  exact-version public-PyPI installation smoke after manual Trusted Publishing
- Seven canonical agent-use eval cases for forecast vintages and the new archive/analysis routes,
  bringing the evaluation catalog to 70 cases
- Reproducible evaluation of FastMCP BM25 progressive discovery on all 70 canonical cases; the
  full MCP tool catalog remains the default pending evidence of improved model routing

### Changed

- Upgrade to stable FastMCP 4.0.10 for MCP `2026-07-28`; the stable runtime retains legacy
  protocol negotiation and removes the beta-only dependency allowlist
- CI rejects all prerelease packages in the lockfile, and the dependency canary checks only newer
  stable FastMCP 4.0.x patches
- Tool descriptions more clearly distinguish historical retrieval, statistical analysis, and
  local-store operations to improve discovery without adding duplicate tool paths
- The live DDS canary accepts schema-valid empty rolling windows while checking half-open range
  filtering and completeness metadata, rather than requiring an in-window publication each run
- Credential-free AESO public report and asset streams are capped at 25 MiB and 384 MiB before
  full-response assembly
- Branch-aware coverage floor raised from 65% to 75%

### Fixed

- Point-in-time forecast queries exclude vintages with unknown or later information timestamps,
  preventing later publications from entering historical `as_of` results
- Planning-area analysis preserves missing source fields instead of treating them as observed zero
- Point-in-time reads also enforce retrieval boundaries and retain immutable retrieval snapshots,
  preventing later actual/finality enrichment from rewriting earlier query results
- Research manifests hash typed observations when upstream hashes are absent and totally order
  source entries; research summaries preserve unknown finality, missing prices, and partial coverage
- Annual frequency assets fit bounded download/decompression limits verified against official
  files; large planning-area and frequency parsing runs outside the async event loop
- Market-event provenance parameters now emit a recursive JSON-value schema instead of an
  unconstrained `{}` schema, preserving JSON structure in client-visible tool output validation

## [0.3.0] - 2026-08-26

### Added

- Official individual-asset Historical CSD Generation Data at hourly and five-minute resolution,
  with fixed-MST-to-UTC DST-safe normalization and source provenance
- Optional incremental DuckDB index, source manifests, and partitioned Parquet snapshots for CSD
  generation, Pool Price, and AIL actual/forecast observations
- General official forecast retrieval for AIL, Pool Price, wind, solar, and current combined
  wind/solar, plus public hourly UC settlement summary
- Official Supply Adequacy / Market Supply Cushion, Supply Surplus, FFR Net Schedule, Dispatch
  Down Service availability, TMR reference-price, and AIES system-event reports
- Gross asset Pool Price energy revenue, CSD-versus-metered comparison, cadence-aware ramp, and
  supply-surplus event association analytics
- Deterministic price distribution, duration curve, market-event, capture-price, net-load,
  supply-stack, intertie, generation, dispatch, outage-association, and forecast-error analytics
- Public active/standby operating-reserve prices, seven-day volume forecast, standby activations,
  and product-level summaries with distinct price concepts
- Sixty-three-case canonical agent-use evaluation catalog with arguments, datasets, caveats, prohibited
  interpretations and numerical relationships; an external-result scorer; a generated MCP
  catalog; and a strict MkDocs documentation website with client configs, investigations, and
  methodology

### Changed

- Historical Pool Price and AIL reads/analytics now prefer only complete, non-preliminary local
  DuckDB coverage; storage/archive work runs off the async event loop and sync is chunked
- Market-event and supply-tightness evidence now includes distinct optional official forecast,
  adequacy, surplus, FFR, DDS, TMR, and system-event context without turning missing data into zero
- Non-loopback HTTP transport refuses to start without bearer authentication unless an explicit
  insecure-remote override is configured
- MCP surface expanded from 27 to 57 tools and from 20 to 29 resources
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

[Unreleased]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/bchoi-qwe/aeso-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/bchoi-qwe/aeso-mcp/releases/tag/v0.1.0
