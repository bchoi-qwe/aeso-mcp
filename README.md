# aeso-mcp

<!-- mcp-name: io.github.bchoi-qwe/aeso-mcp -->

**Agent-native, strongly typed access and analytics for Alberta's electricity market using official AESO data.**

> Independent open-source project. **Not affiliated with or endorsed by the Alberta Electric System Operator (AESO).**

## What it is

`aeso-mcp` is a Model Context Protocol (MCP) server that exposes Alberta electricity-market observations and deterministic analytics to AI clients. It is designed for energy analysts, researchers, developers, journalists, market participants, and AI agents that need reliable, structured AESO data—not a thin REST decorator layer.

## Features

- Typed MCP tools with Pydantic inputs/outputs and structured results
- Current market snapshot combining price, load, generation, interchange, and reserves
- Historical Pool Price and System Marginal Price retrieval with explicit units and timezones
- Deterministic analytics: period comparison, price-event detection, condition evidence
- GridStatus-backed AESO adapters plus a direct APIM httpx client for contracts (not a full second production stack)
- Query bounds, caching, retries, and secret-safe error handling
- Resources for glossary, dataset catalog, and methodology notes

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
| Approved Tx outages | `get_approved_transmission_outages` | AESO-approved planned transmission outages |
| Long-range Tx outages | `get_long_range_transmission_outages` | Tentative ~24-month significant outages |
| MCSINR | `get_monthly_cumulative_net_revenue` | Cumulative net revenue vs offer-cap trigger |
| Secondary offer limit | `get_secondary_offer_price_limit` | Whether secondary offer cap is in effect |
| Assets | `get_assets` | Registry with filters |

Analytics: `compare_market_periods`, `find_price_events`, `explain_market_conditions`,
`compare_forecast_to_actual`.

## Architecture

```text
MCP clients
    |
    v
FastMCP adapter (aeso_mcp/mcp)
    |
    v
Domain services (market, grid, assets, analytics)
    |
    +------------------+
    |                  |
    v                  v
GridStatus provider    Direct AESO APIM (httpx)
    |                  |
    +---------+--------+
              |
              v
          AESO APIs
```

Domain code does not depend on FastMCP. Framework changes should stay in `aeso_mcp/mcp/`.

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
| `get_assets` | Asset registry |
| `compare_market_periods` | Aggregate period comparison |
| `find_price_events` | High-price event detection |
| `explain_market_conditions` | Structured evidence (not causal prose) |
| `compare_forecast_to_actual` | AIL forecast vs actual accuracy |

All tools are read-only, non-destructive, and network-dependent.

## Resources

| URI | Content |
| --- | --- |
| `aeso://glossary` | Market terminology |
| `aeso://datasets` | Dataset catalog |
| `aeso://methodology/pool-price` | Pool Price interpretation |
| `aeso://methodology/system-marginal-price` | SMP interpretation |

## Data semantics

- **Timezone**: `America/Edmonton` (AESO market time). DST days may have 23 or 25 local hours.
- **Intervals**: Explicit `interval_start` / `interval_end` (half-open ranges in requests).
- **Units**: Pool Price / SMP → CAD/MWh; load / generation / interchange / reserves → MW.
- **Status**: Metadata includes `actual` / `forecast` / etc. Forecasts are never implied to be settled actuals.
- **Finality**: Operational feeds may be preliminary; do not assume final settlement.

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
- See [docs/data-sources.md](docs/data-sources.md) for APIM vs public-report backlog (MCSINR, secondary offer cap, merit order via APIM)
- Merit order, metered volumes, and unit commitments via **APIM** (do not scrape ETS copies)
- Optional DuckDB/Parquet historical analytics store
- Broader forecast vs actual tools

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project is an independent open-source interface to publicly documented AESO APIs. It is **not** an official AESO product and is **not affiliated with or endorsed by AESO**. Market data may be preliminary or incomplete; verify critical decisions against official AESO publications.
