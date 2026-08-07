# Security Policy

## Supported versions

Security fixes are applied to the latest published release on the `main` branch.

## Threat model (summary)

`aeso-mcp` is a **read-only** MCP server for public Alberta electricity-market data.

In scope:

- Theft or leakage of `AESO_API_KEY` via logs, exceptions, or tool output
- Prompt/tool abuse attempting arbitrary network fetch, shell, filesystem, or SQL execution
- Oversized upstream queries that could DoS the server or the AESO gateway
- Dependency supply-chain vulnerabilities

Out of scope / residual risk:

- Integrity of AESO-published operational data
- Confidentiality of public market data
- Compromise of the host MCP client environment

## Controls

- Secrets only via environment / `.env` (never committed)
- API keys are `SecretStr` and must not appear in logs or client error messages
- No generic URL-fetch, shell, filesystem, SQL, or code-execution tools
- Upstream host allow-list limited to AESO APIM
- Bounded date ranges, observation caps, HTTP timeouts, and selective retries
- Stdio logging goes to **stderr** only

## Reporting a vulnerability

Please open a private GitHub security advisory on this repository, or email the maintainer listed in `pyproject.toml`.

Do not open a public issue for undisclosed vulnerabilities.

Include:

1. Affected version / commit
2. Impact description
3. Reproduction steps
4. Any suggested fix

We aim to acknowledge reports within 7 days.
