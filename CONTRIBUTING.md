# Contributing

Thanks for contributing to `aeso-mcp`.

## Development setup

```bash
git clone https://github.com/brandonchoi/aeso-mcp.git
cd aeso-mcp
uv sync --group dev
cp .env.example .env   # add AESO_API_KEY for live checks
```

Obtain an API key from the [AESO developer portal](https://developer-apim.aeso.ca/).

## Checks

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest tests/unit tests/contract tests/mcp --cov=aeso_mcp
uv build
```

Live AESO tests (optional):

```bash
AESO_API_KEY=... uv run pytest tests/integration -m integration
```

## Design guidelines

- Keep FastMCP code inside `aeso_mcp/mcp/`
- Keep AESO HTTP/GridStatus details inside `aeso_mcp/providers/`
- Prefer typed Pydantic models for tool I/O
- Prefer GridStatus when it already covers a dataset
- Document timezone, units, and forecast vs actual semantics
- Add contract fixtures for new upstream payloads

## Pull requests

1. Keep changes focused
2. Add/adjust tests
3. Update `CHANGELOG.md` under Unreleased
4. Do not commit secrets

## MCP / FastMCP version policy

- Normal CI uses the locked dependency graph (`uv.lock`)
- FastMCP 4.x may be pinned to a prerelease while targeting MCP `2026-07-28`
- Dependency canary workflow may test newer allowed versions without blocking merge
- Do not auto-merge major FastMCP / MCP SDK upgrades
