# Getting started

## Requirements

- Python 3.13 or newer
- An AESO APIM subscription key **only** for authenticated market datasets
- An MCP-capable client

## Run from PyPI

```bash
uvx aeso-mcp  # works for public reports and CSD archives without a key
# For complete market coverage, set AESO_API_KEY=your-key before launching.
```

The optional historical store uses DuckDB and PyArrow:

```bash
uvx --from 'aeso-mcp[analytics]' aeso-mcp
```

The API key is optional for ETS public reports, renewables and Pool Price forecast publications,
and official historical CSD data. Live prices, load, current CSD, and authenticated operational
reports need a key. Without the analytics extra, official CSD archive queries still work
directly; store sync/status reports
that persistence is unavailable.

## MCP client configuration

```json
{
  "mcpServers": {
    "aeso": {
      "command": "uvx",
      "args": ["--from", "aeso-mcp[analytics]", "aeso-mcp"],
      "env": {"AESO_API_KEY": "your-key"}
    }
  }
}
```

Copy-paste configurations for Codex, the ChatGPT desktop app, Claude Desktop, Claude Code, and
Cursor are maintained in [MCP client configurations](client-configs.md).

## Historical store location

By default, DuckDB and Parquet files live under `~/.cache/aeso-mcp/history`. Override the root:

```bash
export AESO_MCP_HISTORY_STORE_PATH=/absolute/path/to/aeso-history
```

The server exposes no arbitrary SQL tool. Reads and writes go through typed dataset operations.

## Develop locally

```bash
uv sync --locked --group dev --extra analytics --extra docs
cp .env.example .env
uv run aeso-mcp
```

Build the documentation website with `uv run mkdocs build --strict` and preview it with
`uv run mkdocs serve`.
