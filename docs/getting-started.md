# Getting started

## Requirements

- Python 3.13 or newer
- An AESO APIM subscription key
- An MCP-capable client

## Run from PyPI

```bash
export AESO_API_KEY=your-key
uvx aeso-mcp
```

The optional historical store uses DuckDB and PyArrow:

```bash
uvx --from 'aeso-mcp[analytics]' aeso-mcp
```

Without that extra, official CSD archive queries still work directly; store sync/status reports
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
