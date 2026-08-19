# aeso-mcp

<!-- mcp-name: io.github.bchoi-qwe/aeso-mcp -->

**Agent-native, strongly typed access and analytics for Alberta's electricity market using official AESO data.**

> Independent open-source project. **Not affiliated with or endorsed by the Alberta Electric System Operator (AESO).**

## What it is

`aeso-mcp` is a Model Context Protocol (MCP) server that exposes Alberta electricity-market observations and deterministic analytics to AI clients. It is designed for energy analysts, researchers, developers, journalists, market participants, and AI agents that need reliable, structured AESO data—not a thin REST decorator layer.

## Features

- Typed MCP tools with Pydantic inputs/outputs and structured results
- Current market snapshot combining price, load, generation, interchange, and reserves
- Paginated historical Pool Price, System Marginal Price, load, and generation retrieval
- Authenticated APIM reports for merit order, commitments, capability/outages, interties,
  metered volumes, and operating-reserve offer control
- Deterministic analytics: compact history summaries, period comparison, event detection,
  condition evidence, forecast accuracy, and transparent supply-tightness indicators
- One complete server package and startup path: `AESO_API_KEY` is always required; there is no
  reduced credential-free server mode
- Query bounds, cache provenance, completeness metadata, upstream `Retry-After` handling, and
  secret-safe machine-readable errors
- Hardened HTTP transport with Host/Origin validation, optional bearer authentication, rate and
  concurrency limits, request-size bounds, probes, and correlation IDs
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

Analytics: `summarize_market_history`, `assess_supply_tightness`, `compare_market_periods`,
`find_price_events`, `explain_market_conditions`, and `compare_forecast_to_actual`.

## Architecture

```text
MCP clients
    |
    v
FastMCP adapter (aeso_mcp/mcp)
    |
    v
Domain services (market, grid, assets, operations, analytics, transmission, market power)
    |
    +---------------------+------------------------+
    |                     |                        |
    v                     v                        v
GridStatus provider    Direct AESO APIM    Public-reports client
    |                     |                        |
    +----------+----------+                        |
               |                                   |
               v                                   v
       AESO APIM gateway                       ets.aeso.ca
```

Domain code does not depend on FastMCP. Framework changes should stay in `aeso_mcp/mcp/`.
Both upstream clients are implementation details of this single server: the APIM key is sent only
to `apimgw.aeso.ca` and is never sent to the allow-listed `ets.aeso.ca` report host.

## Requirements

- Python 3.13+
- AESO APIM API key from [developer-apim.aeso.ca](https://developer-apim.aeso.ca/)
- [`uv`](https://docs.astral.sh/uv/) recommended

## Installation

See [LIMITATIONS.md](LIMITATIONS.md) for an honest gap inventory. **PyPI publication is deferred** until after human review.

### From GitHub (current)

Until the package is published to PyPI:

```bash
export AESO_API_KEY=your-key
uvx --from git+https://github.com/bchoi-qwe/aeso-mcp.git aeso-mcp
```

Or install editable for development:

```bash
git clone https://github.com/bchoi-qwe/aeso-mcp.git
cd aeso-mcp
uv sync --group dev
cp .env.example .env   # set AESO_API_KEY
uv run aeso-mcp
```

### From PyPI (deferred)

Not published yet. After review and an intentional publish, install with:

```bash
export AESO_API_KEY=your-key
uvx aeso-mcp
```

### Docker

```bash
docker build -t aeso-mcp .
docker run --rm -e AESO_API_KEY=your-key -p 8000:8000 aeso-mcp
```

## Obtaining an AESO API key

1. Register at the [AESO developer portal](https://developer-apim.aeso.ca/)
2. Subscribe to the AESO public API product
3. Copy the primary/secondary subscription key
4. Set `AESO_API_KEY` in your environment (never commit it)

Missing credentials produce an actionable startup error. The key is never returned through MCP tools or logged.

## Example MCP client configuration

### Cursor / Claude Desktop style (stdio)

```json
{
  "mcpServers": {
    "aeso": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/bchoi-qwe/aeso-mcp.git", "aeso-mcp"],
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

HTTP always validates Host and Origin. For a remotely reachable deployment, explicitly set
`AESO_MCP_HTTP_ALLOWED_HOSTS` and `AESO_MCP_HTTP_ALLOWED_ORIGINS`; set
`AESO_MCP_HTTP_BEARER_TOKEN` to require bearer authentication. `/healthz` and `/readyz` contain no
market data or secrets. See [.env.example](.env.example) for all bounded runtime settings.

## Example prompts

- What is Alberta's current grid situation?
- What is the current pool price?
- Show Alberta pool prices over the last 24 hours.
- Compare today's pool prices with yesterday's.
- Which hours had the highest prices this week?
- How much wind and solar are producing right now?
- What happened during the largest price spike this week?
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

All tools are read-only, non-destructive, and network-dependent.

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
- **Completeness**: Metadata reports available/missing series and expected/missing observations
  where the source cadence is known. Optional enrichment failures are surfaced as partial or
  degraded results with warnings.
- **Pagination**: Raw price, SMP, load, generation, and operational reports return `page` metadata
  with `next_offset`. Use `summarize_market_history` before retrieving long raw series.
- **Cache timing**: `retrieved_at` identifies the upstream fetch; `served_at`, `cache_hit`, and
  `cache_age` identify when and how the response was served.

## Development

```bash
uv sync --group dev
uv run ruff check src tests
uv run pyright src
uv run pytest tests/unit tests/contract tests/mcp --cov=aeso_mcp
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

## Roadmap

- Human review against [LIMITATIONS.md](LIMITATIONS.md) before any PyPI / MCP Registry publish
- Optional DuckDB/Parquet historical analytics store
- Broader forecast vs actual tools

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project is an independent open-source interface to publicly documented AESO APIs. It is **not** an official AESO product and is **not affiliated with or endorsed by AESO**. Market data may be preliminary or incomplete; verify critical decisions against official AESO publications.
