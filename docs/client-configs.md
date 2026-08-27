# MCP client configurations

All clients start the same published stdio command. Install [`uv`](https://docs.astral.sh/uv/),
set `AESO_API_KEY` in the client environment, and use the analytics extra if you want the local
DuckDB/Parquet store.

```bash
uvx --from 'aeso-mcp[analytics]' aeso-mcp
```

Do not commit a real subscription key. After configuring a client, ask it to list MCP tools and
confirm that `get_market_snapshot`, `get_historical_generation`, `get_forecast`,
`get_supply_adequacy`, and `summarize_operating_reserve_market` are present before starting an
investigation.

## Codex and the ChatGPT desktop app

Codex reads user configuration from `~/.codex/config.toml`; a trusted project may instead use
`.codex/config.toml`. The checked-in [Codex template](configs/aeso.codex.toml) forwards an existing
environment variable rather than storing its value:

```toml
[mcp_servers.aeso]
command = "uvx"
args = ["--from", "aeso-mcp[analytics]", "aeso-mcp"]
env_vars = ["AESO_API_KEY"]
startup_timeout_sec = 60
tool_timeout_sec = 300
```

The equivalent one-time CLI registration is:

```bash
codex mcp add aeso --env AESO_API_KEY=your-key -- uvx --from 'aeso-mcp[analytics]' aeso-mcp
codex mcp list
```

Codex CLI, the IDE extension, and the ChatGPT desktop app share this configuration. OpenAI's
[Codex MCP documentation](https://developers.openai.com/codex/mcp) is the source of truth for
configuration fields and the current settings UI.

## Claude Desktop

Add the `aeso` entry to the `mcpServers` object in Claude Desktop's configuration. On macOS the
file is normally `~/Library/Application Support/Claude/claude_desktop_config.json`. Merge the
entry with any existing servers instead of replacing the complete file.

```json
{
  "mcpServers": {
    "aeso": {
      "command": "uvx",
      "args": ["--from", "aeso-mcp[analytics]", "aeso-mcp"],
      "env": {
        "AESO_API_KEY": "your-key"
      }
    }
  }
}
```

Restart Claude Desktop after saving the file. See Anthropic's
[MCP setup documentation](https://docs.anthropic.com/en/docs/claude-code/mcp) for the current
configuration and troubleshooting flow.

## Claude Code

Register the same stdio server at user scope when it should be available across projects:

```bash
claude mcp add --scope user --transport stdio aeso --env AESO_API_KEY=your-key -- uvx --from 'aeso-mcp[analytics]' aeso-mcp
claude mcp list
```

Use project scope only when the configuration belongs to that repository, and do not check a
literal key into its configuration.

## Cursor

Copy the checked-in [JSON template](configs/aeso.mcp.json) to `.cursor/mcp.json` for one project or
merge its `aeso` entry into `~/.cursor/mcp.json` for user-wide access:

```json
{
  "mcpServers": {
    "aeso": {
      "command": "uvx",
      "args": ["--from", "aeso-mcp[analytics]", "aeso-mcp"],
      "env": {
        "AESO_API_KEY": "your-key"
      }
    }
  }
}
```

Cursor's [MCP documentation](https://docs.cursor.com/context/model-context-protocol) describes the
current project and global configuration locations.

## Validate any client

1. Confirm `uvx --from 'aeso-mcp[analytics]' aeso-mcp --help` succeeds in a terminal.
2. Confirm the client discovers the complete AESO MCP tool catalog.
3. Call `get_market_snapshot` and inspect metadata and warnings as well as values.
4. Call `get_historical_store_status`; `enabled: true` confirms the analytics extra loaded.
5. Keep current observations, operational history, forecasts, and settlement publications
   distinct in downstream answers.

If startup fails, check that the client can find `uvx` and inherits `AESO_API_KEY`. The server
never returns or logs the key, and it sends the key only to the allow-listed AESO APIM host.
