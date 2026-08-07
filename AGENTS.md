# Agent notes for aeso-mcp

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and checks.

## Layout

- `src/aeso_mcp/mcp/` — FastMCP adapter only
- `src/aeso_mcp/providers/` — GridStatus + AESO APIM
- `src/aeso_mcp/services/` — domain logic / analytics
- `src/aeso_mcp/models/` — Pydantic schemas

## Conventions

- Prefer GridStatus for AESO datasets it already supports
- Keep tools intent-oriented, typed, bounded, and timezone-aware (`America/Edmonton`)
- Never log or return `AESO_API_KEY`
- Do not add agent frameworks (LangChain, etc.) — this server provides tools to agents
