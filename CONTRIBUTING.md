# Contributing

Thanks for contributing to `aeso-mcp`.

## Development setup

```bash
git clone https://github.com/bchoi-qwe/aeso-mcp.git
cd aeso-mcp
uv sync --group dev --extra analytics --extra docs
cp .env.example .env   # add AESO_API_KEY for live checks
```

Obtain an API key from the [AESO developer portal](https://developer-apim.aeso.ca/).

## Checks

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pyright src
uv run pytest tests/unit tests/contract tests/mcp tests/evals --cov=aeso_mcp
uv run python scripts/generate_catalog.py --check
uv run mkdocs build --strict
uv run python tests/packaging/check_release_metadata.py
uv build
```

Live AESO tests (optional):

```bash
AESO_API_KEY=... uv run pytest tests/integration -m integration
```

The scheduled live canary uses the protected `aeso-live` GitHub environment and its
`AESO_API_KEY` secret. Pull-request CI remains deterministic and does not contact AESO.

## Design guidelines

- Keep FastMCP code inside `aeso_mcp/mcp/`
- Keep AESO HTTP/GridStatus details inside `aeso_mcp/providers/`
- Keep DuckDB/Parquet implementation details inside `aeso_mcp/storage/`; expose typed operations,
  never arbitrary SQL
- Prefer typed Pydantic models for tool I/O
- Prefer GridStatus when it already covers a dataset
- Document timezone, units, and forecast vs actual semantics
- Add contract fixtures for new upstream payloads
- Add or update canonical cases in `tests/evals/cases.json` for new agent-facing capabilities
- Keep the server a single API-key-required package; source-specific upstream clients are internal
  implementation details, not alternate server modes

## Adding an AESO dataset

1. Add the proposed product to `docs/data-sources.md`. Confirm whether AESO APIM, an existing
   GridStatus adapter, a named ETS machine-readable report, or another official fixed source owns
   the data. Do not add an ETS scraper for an APIM product.
2. Model the source-specific request and response semantics: timezone, interval boundaries, units,
   observation type, finality, completeness, publication delay, and bounded date range.
3. Implement source parsing in `providers/`. APIM uses the authenticated client; ETS and archives
   use their separate credential-free allow-listed clients. Never accept an arbitrary URL or send
   `AESO_API_KEY` outside the APIM host.
4. Put reusable joins and calculations in `services/`, with Pydantic contracts in `models/`.
   FastMCP registration belongs only in `mcp/`. Keep arbitrary SQL and provider payloads out of the
   public tool surface.
5. Add source-shape contract fixtures, boundary/unit tests, MCP discovery coverage, and at least one
   canonical `tests/evals/cases.json` scenario covering routing, arguments, source selection,
   caveats, prohibited interpretations, and numerical relationships.
6. Add a narrow opt-in integration canary when a public or protected live call can detect schema
   drift without excessive upstream traffic.
7. Update methodology/capability resources, limitations, the Unreleased changelog, and regenerate
   `docs/generated/mcp-catalog.md`. Run every check above after the final edit.

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

## Pre-PyPI checklist

Do **not** publish to PyPI or the MCP Registry until a human has signed off on:

1. [LIMITATIONS.md](LIMITATIONS.md) reviewed and still accurate
2. Live smoke with a real `AESO_API_KEY` (`pytest tests/integration -m integration`)
3. Tool/resource surface matches README (no silent stubs presented as working)
4. Secret hygiene: no keys in git, logs, or chat history (rotate if exposed)
5. Publish workflow remains **manual** (`workflow_dispatch` only)
6. Version/CHANGELOG cut intentionally for the published tag
