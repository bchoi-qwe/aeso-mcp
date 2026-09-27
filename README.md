# aeso-mcp

<!-- mcp-name: io.github.bchoi-qwe/aeso-mcp -->

**Agent-native, strongly typed access and analytics for Alberta's electricity market using official AESO data.**

> Independent open-source project. **Not affiliated with or endorsed by the Alberta Electric System Operator (AESO).**

## What it is

`aeso-mcp` is a Model Context Protocol (MCP) server that exposes Alberta electricity-market observations and deterministic analytics to AI clients. It is designed for energy analysts, researchers, developers, journalists, market participants, and AI agents that need reliable, structured AESO data—not a thin REST decorator layer.

## Features

- Typed MCP tools with Pydantic inputs/outputs and structured results
- Stable FastMCP 4.0.10 runtime targeting MCP `2026-07-28`, with FastMCP's per-connection
  negotiation for legacy session-based clients
- Current market snapshot combining price, load, generation, interchange, and reserves
- Paginated historical Pool Price, System Marginal Price, load, and generation retrieval
- Official individual-asset Historical CSD Generation Data at hourly and five-minute resolution
- Optional incremental DuckDB index and partitioned Parquet snapshots for repeatable research
- Official AIL, Pool Price, wind, solar, and combined wind/solar forecast publications through
  one typed `get_forecast` contract, with persisted publication vintages and point-in-time
  `as_of` selection that excludes unknown publication chronology
- Official supply-adequacy, supply-surplus, FFR Net Schedule, Dispatch Down Service, TMR
  reference-price, and AIES system-event reports
- Official historical adequacy/cushion web codes, transmission outages, planning-area hourly
  load/generation, constrained volume, EEA events, operating-reserve directives, and the current
  Pool Participant registry through one typed research-archive contract
- Authenticated APIM reports for merit order, commitments, capability/outages, interties,
  metered volumes, and operating-reserve offer control
- Deterministic analytics: compact history summaries, period comparison, event detection,
  condition evidence, price distributions, capture price, net load, supply stack, generation,
  outage association, generalized forecast accuracy, asset gross energy revenue, CSD-versus-
  metered comparisons, ramps, supply-surplus associations, and transparent supply-tightness
  indicators, plus participant concentration, regional, constrained-volume, scarcity, and compact
  system-frequency analysis
- Deterministic research manifests identify exact input publications, parameters, methodology,
  completeness, and degradation warnings for multi-source market-event analysis
- Public operating-reserve active/standby prices, seven-day forecasts, activations, and summaries
- One complete server package and startup path: `AESO_API_KEY` is always required; there is no
  reduced credential-free server mode
- Query bounds, cache provenance, completeness metadata, upstream `Retry-After` handling, and
  secret-safe machine-readable errors
- Hardened HTTP transport with Host/Origin validation, bearer authentication required for
  non-loopback binds by default, rate/concurrency limits, request-size bounds, probes, and
  correlation IDs
- Reusable MCP prompts plus glossary, capability, dataset, and methodology resources

## Implemented datasets

| Dataset | Tool | Notes |
| --- | --- | --- |
| Market snapshot | `get_market_snapshot` | Current cohesive view |
| Pool Price | `get_pool_prices` | Hourly CAD/MWh |
| System Marginal Price | `get_system_marginal_prices` | Minute-level CAD/MWh |
| Alberta Internal Load | `get_load` | MW; optional forecast |
| Generation / fuel mix | `get_generation` | Current all fuels; historical wind/solar |
| Interchange | `get_interchange` | Current path flows MW |
| Operating reserves | `get_reserves` | Current MW indicators |
| Generator outages | `get_outages` | Hourly outage capacity by fuel/technology |
| AIES capacity/outages | `get_generation_capacity` | Hourly MC, AC, operating, and mothball outage MW |
| Load outage forecast | `get_load_outage_forecast` | Hourly forecast MW |
| Energy Merit Order | `get_energy_merit_order` | Historical blocks; 60-day publication delay |
| Unit commitments | `get_unit_commitments` | Generating-unit commitment directives |
| Intertie capability | `get_intertie_capability` | Import/export ATC, TTC, margins, gross offers |
| Intertie capability outages | `get_intertie_outages` | Outages affecting interties/flowgates |
| Metered volumes | `get_metered_volumes` | Hourly MWh by asset; optional ID filters |
| OR offer control | `get_operating_reserve_offer_control` | Historical reserve offer blocks; 60-day delay |
| Approved Tx outages | `get_approved_transmission_outages` | AESO-approved planned transmission outages |
| Long-range Tx outages | `get_long_range_transmission_outages` | Tentative ~24-month significant outages |
| MCSINR | `get_monthly_cumulative_net_revenue` | Cumulative net revenue vs offer-cap trigger |
| Secondary offer limit | `get_secondary_offer_price_limit` | Whether secondary offer cap is in effect |
| Assets | `get_assets` | Registry with filters |
| Historical CSD generation | `get_historical_generation` | Individual assets; hourly / five-minute operational MW |
| Official forecasts | `get_forecast` | AIL / Pool Price / wind / solar; source-specific horizons and cadence |
| Official research archive | `get_research_data` | Historical adequacy/cushion, transmission, planning-area, constraint, EEA, OR-directive, and current participant records |
| Supply adequacy / cushion | `get_supply_adequacy` | Official hourly categorical status bands |
| Supply surplus | `get_supply_surplus` | Official hourly zero-price forecast status |
| FFR Net Schedule | `get_ffr_net_schedule` | Hourly scheduled imports (negative) / exports (positive) MW |
| Dispatch Down Service | `get_dispatch_down_service` | Published DDS availability MW |
| TMR reference price | `get_tmr_reference_price` | Published CAD/MWh value by effective date |
| AIES system events | `get_system_events` | Bounded event-log comments; no inferred event end |
| UC settlement summary | `get_uc_settlement_summary` | Hourly CAD amount and charged MW |
| OR prices | `get_operating_reserve_prices` | Active and standby price components and volumes |
| OR forecast | `get_operating_reserve_forecast` | Current seven-day hourly MW forecast |
| OR activations | `get_operating_reserve_activations` | Hourly standby volume and activation price |

Analytics: `summarize_market_history`, `assess_supply_tightness`, `compare_market_periods`,
`find_price_events`, `explain_market_conditions`, `compare_forecast_to_actual`,
`get_price_statistics`, `get_price_duration_curve`, `analyze_market_event`,
`calculate_capture_prices`, `analyze_net_load`, `analyze_supply_stack`,
`analyze_intertie_utilization`, `analyze_generation_mix`, `analyze_asset_dispatch`,
`analyze_outage_impact`, `analyze_forecast_error`, `calculate_asset_energy_revenue`,
`compare_csd_to_metered`, `analyze_ramps`, `analyze_supply_surplus_events`,
`summarize_operating_reserve_market`, `analyze_participant_concentration`,
`analyze_regional_load_generation`, `analyze_constrained_volume`, `analyze_scarcity`, and
`analyze_system_frequency`.

## Architecture

The documentation site includes a full [architecture and MCP Inspector walkthrough](docs/architecture.md).

```text
MCP clients
    |
    v
FastMCP adapter (aeso_mcp/mcp)
    |
    v
Domain services (market, grid, history, forecasts, reports, research, reserves, operations)
    |
    +---------------+----------------+----------------+----------------+
    |               |                |                |
    v               v                v                v
GridStatus     Direct APIM     Public reports    CSD archive
    |               |           (ets.aeso.ca)      (Box)
    +-------+-------+
            |
            v
      AESO APIM gateway
```

Domain code does not depend on FastMCP. Framework changes should stay in `aeso_mcp/mcp/`.
All upstream clients are implementation details of this single server: the APIM key is sent only
to `apimgw.aeso.ca` and is never sent to the allow-listed ETS or Box hosts.

## Requirements

- Python 3.13+
- AESO APIM API key from [developer-apim.aeso.ca](https://developer-apim.aeso.ca/)
- [`uv`](https://docs.astral.sh/uv/) recommended

## Installation

See [LIMITATIONS.md](LIMITATIONS.md) for an honest gap inventory.

### From PyPI (recommended)

```bash
export AESO_API_KEY=your-key
uvx aeso-mcp
```

For incremental DuckDB/Parquet historical storage:

```bash
uvx --from 'aeso-mcp[analytics]' aeso-mcp
```

### From GitHub

To run the current repository version directly:

```bash
export AESO_API_KEY=your-key
uvx --from git+https://github.com/bchoi-qwe/aeso-mcp.git aeso-mcp
```

### Development

```bash
git clone https://github.com/bchoi-qwe/aeso-mcp.git
cd aeso-mcp
uv sync --group dev
cp .env.example .env   # set AESO_API_KEY
uv run aeso-mcp
```

### Docker

```bash
docker build -t aeso-mcp .
docker run --rm \
  -e AESO_API_KEY=your-key \
  -e AESO_MCP_HTTP_BEARER_TOKEN=replace-with-a-long-random-token \
  -p 8000:8000 \
  aeso-mcp
```

Send the same token in the MCP client's `Authorization: Bearer ...` header. The image binds to
`0.0.0.0`, so it intentionally requires bearer authentication by default.

## Obtaining an AESO API key

1. Register at the [AESO developer portal](https://developer-apim.aeso.ca/)
2. Subscribe to the AESO public API product
3. Copy the primary/secondary subscription key
4. Set `AESO_API_KEY` in your environment (never commit it)

Missing credentials produce an actionable startup error. The key is never returned through MCP tools or logged.

## Example MCP client configuration

See the documentation site's [MCP client configurations](docs/client-configs.md) for maintained
Codex, ChatGPT desktop app, Claude Desktop, Claude Code, and Cursor setup instructions.

### Cursor / Claude Desktop style (stdio)

```json
{
  "mcpServers": {
    "aeso": {
      "command": "uvx",
      "args": ["aeso-mcp"],
      "env": {
        "AESO_API_KEY": "your-key"
      }
    }
  }
}
```

### HTTP transport

```bash
uv run aeso-mcp --transport http --host 127.0.0.1 --port 8000
```

HTTP always validates Host and Origin. A non-loopback bind now refuses to start unless
`AESO_MCP_HTTP_BEARER_TOKEN` is configured. An explicit
`AESO_MCP_HTTP_ALLOW_INSECURE_REMOTE=true` override exists for isolated environments that accept
the risk; it is never the default. Also set `AESO_MCP_HTTP_ALLOWED_HOSTS` and
`AESO_MCP_HTTP_ALLOWED_ORIGINS` for the deployment. `/healthz` and `/readyz` contain no market data
or secrets. See [.env.example](.env.example) for all bounded runtime settings.

## Example prompts

- What is Alberta's current grid situation?
- What is the current pool price?
- Show Alberta pool prices over the last 24 hours.
- Compare today's pool prices with yesterday's.
- Which hours had the highest prices this week?
- How much wind and solar are producing right now?
- What happened during the largest price spike this week?
- How inaccurate was the seven-day wind forecast during that event?
- Was AESO publishing a supply-surplus or tight supply-cushion status at the time?
- What gross Pool Price energy revenue did this asset's metered MWh earn?
- Explain the evidence associated with today's price increase.

## Tools

| Tool | Purpose |
| --- | --- |
| `get_market_snapshot` | Current market overview |
| `get_pool_prices` | Hourly Pool Price history |
| `get_system_marginal_prices` | Minute-level SMP history |
| `get_load` | Alberta Internal Load |
| `get_generation` | Fuel mix / renewable history |
| `get_interchange` | Intertie flows |
| `get_reserves` | Operating reserve indicators |
| `get_outages` | Hourly generator outage capacity by fuel |
| `get_generation_capacity` | AIES capability and outage grouping by fuel |
| `get_load_outage_forecast` | Hourly load-outage forecast |
| `get_energy_merit_order` | Historical energy merit-order blocks |
| `get_unit_commitments` | Generating-unit commitment directives |
| `get_intertie_capability` | Intertie/flowgate ATC, TTC, and margins |
| `get_intertie_outages` | Outages affecting intertie capability |
| `get_metered_volumes` | Metered energy by asset |
| `get_operating_reserve_offer_control` | Historical reserve offer-control blocks |
| `get_approved_transmission_outages` | Approved planned transmission outages |
| `get_long_range_transmission_outages` | Tentative long-range transmission outages |
| `get_assets` | Asset registry |
| `get_monthly_cumulative_net_revenue` | Current MCSINR publication |
| `get_secondary_offer_price_limit` | Current secondary offer-cap status |
| `compare_market_periods` | Aggregate period comparison |
| `find_price_events` | High-price event detection |
| `explain_market_conditions` | Structured evidence (not causal prose) |
| `compare_forecast_to_actual` | AIL forecast vs actual accuracy |
| `summarize_market_history` | Compact hourly/daily/weekly/monthly price and load summaries |
| `assess_supply_tightness` | Transparent reserve-adjusted supply-margin screening |
| `get_historical_generation` | Official individual-asset CSD archive history |
| `sync_historical_store` | Incremental local DuckDB/Parquet ingestion |
| `get_historical_store_status` | Local coverage, manifests, and partition status |
| `get_forecast` | Official AIL, Pool Price, wind, solar, and combined wind/solar forecasts |
| `get_research_data` | Bounded page from one verified official historical/spatial/reliability archive |
| `get_supply_adequacy` | Official supply-adequacy and market-supply-cushion status bands |
| `get_supply_surplus` | Official hourly supply-surplus forecast status |
| `get_ffr_net_schedule` | Historical scheduled FFR intertie transfer |
| `get_dispatch_down_service` | Dispatch Down Service availability report |
| `get_tmr_reference_price` | Published TMR reference price |
| `get_system_events` | Bounded AIES Event Log messages |
| `get_uc_settlement_summary` | Public hourly UC amount and charged volume |
| `get_price_statistics` | Price distribution and volatility statistics |
| `get_price_duration_curve` | Pool Price exceedance curve |
| `analyze_market_event` | Focus versus baseline multi-series associations |
| `calculate_capture_prices` | Generation-weighted price and capture rate |
| `analyze_net_load` | AIL minus selected renewable generation |
| `analyze_supply_stack` | Historical offer-stack analysis |
| `analyze_intertie_utilization` | Gross-offer to capability proxy |
| `analyze_generation_mix` | CSD energy and share by fuel |
| `analyze_asset_dispatch` | Asset output, capacity factor, and ramps |
| `analyze_outage_impact` | Hourly outage-price association |
| `analyze_forecast_error` | General forecast error, percentiles, market-hour and lead-time profiles |
| `calculate_asset_energy_revenue` | Metered MWh × Pool Price gross energy revenue |
| `compare_csd_to_metered` | Operational CSD output versus metered MWh |
| `analyze_ramps` | Cadence-aware AIL, net-load, renewable, or asset ramps |
| `analyze_supply_surplus_events` | Price/load/renewable associations during explicit surplus states |
| `get_operating_reserve_prices` | Active/standby price components and volumes |
| `get_operating_reserve_forecast` | Seven-day reserve-volume forecast |
| `get_operating_reserve_activations` | Hourly standby activations |
| `summarize_operating_reserve_market` | Product price/volume/activation summary |
| `analyze_participant_concentration` | Offered-volume shares and HHI using current participant mapping |
| `analyze_regional_load_generation` | Planning-area and regional load/generation aggregates |
| `analyze_constrained_volume` | Planning-area/fuel constrained MWh and optional price association |
| `analyze_scarcity` | Historical adequacy/cushion web codes, EEA, and price context |
| `analyze_system_frequency` | Compact six-hour 10-second frequency statistics; no raw row output |

Market and research tools are read-only. `sync_historical_store` writes only to the configured
local historical-store root; it does not mutate AESO or another external system.

## Resources

| URI | Content |
| --- | --- |
| `aeso://glossary` | Market terminology |
| `aeso://datasets` | Dataset catalog |
| `aeso://methodology/pool-price` | Pool Price interpretation |
| `aeso://methodology/system-marginal-price` | SMP interpretation |
| `aeso://capabilities` | Complete tool, prompt, and resource surface |
| `aeso://methodology/{dataset}` | Dataset-specific interpretation and caveats |

Prompts: `daily_market_brief`, `investigate_price_event`, and `compare_market_days`.

## Data semantics

- **Timezone**: `America/Edmonton` (AESO market time). DST days may have 23 or 25 local hours.
- **Intervals**: Explicit `interval_start` / `interval_end` (half-open ranges in requests).
- **Units**: Pool Price / SMP → CAD/MWh; load / generation / interchange / reserves → MW.
- **Status**: Metadata includes `actual` / `forecast` / etc. Forecasts are never implied to be settled actuals.
- **Finality**: Operational feeds may be preliminary; do not assume final settlement.
- **Point-in-time forecasts**: `as_of` selects only vintages with known issue/publication times and retrieval times at
  or before the boundary. Unknown chronology is excluded; target intervals absent from persisted
  history remain unobserved rather than being backfilled from a later publication.
- **Completeness**: Metadata reports available/missing series and expected/missing observations
  where the source cadence is known. Optional enrichment failures are surfaced as partial or
  degraded results with warnings.
- **Pagination**: Raw price, SMP, load, generation, and operational reports return `page` metadata
  with `next_offset`. Use `summarize_market_history` before retrieving long raw series.
- **Cache timing**: `retrieved_at` identifies the upstream fetch; `served_at`, `cache_hit`, and
  `cache_age` identify when and how the response was served.
- **Research identity**: `analyze_market_event.analysis_manifest` records exact sources,
  parameters, methodology version, warnings, and a deterministic SHA-256 `analysis_id`.

## Development

```bash
uv sync --group dev
uv run ruff check src tests scripts
uv run pyright src
uv run pytest tests/unit tests/contract tests/mcp tests/evals --cov=aeso_mcp
uv run python scripts/generate_catalog.py --check
uv run mkdocs build --strict
uv build
```

Optional live tests:

```bash
AESO_API_KEY=... uv run pytest tests/integration -m integration
```

MCP Inspector:

```bash
# Prefer the console entrypoint; or point Inspector at:
# uv run aeso-mcp
npx @modelcontextprotocol/inspector uv run aeso-mcp
```

## Tests

- `tests/unit` — time, bounds, analytics, config
- `tests/contract` — AESO APIM fixtures via `respx`
- `tests/mcp` — tool/resource discovery and structured outputs
- `tests/integration` — opt-in live AESO calls

## Security

See [SECURITY.md](SECURITY.md). Highlights: no arbitrary URL/shell/SQL tools, host allow-list, secret hygiene, bounded queries, stderr logging for stdio.

## Development status

Version 0.4.0 includes the historical store, full CSD generation adapter, official forecast/report
providers, general research analytics, operating-reserve market surface, eval suite, and
documentation site. It also includes point-in-time forecast vintages, reproducibility
manifests, release-policy hardening, and the verified official research-data archive.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project is an independent open-source interface to publicly documented AESO APIs. It is **not** an official AESO product and is **not affiliated with or endorsed by AESO**. Market data may be preliminary or incomplete; verify critical decisions against official AESO publications.
