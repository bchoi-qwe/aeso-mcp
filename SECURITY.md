# Security Policy

## Supported versions

Security fixes are applied to the latest published release on the `main` branch.

## Threat model (summary)

`aeso-mcp` is externally read-only: it never mutates AESO or another upstream system. The optional
`sync_historical_store` operation writes derived/indexed data only under its configured local root.

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
- No generic URL-fetch, shell, filesystem, SQL, or code-execution tools; the one local storage
  operation has a fixed typed dataset contract and configured root
- HTTPS-only authenticated APIM access (`apimgw.aeso.ca`) and an isolated, unauthenticated
  upstream client for public reports (`ets.aeso.ca`) plus a separate fixed-share client for the
  official AESO CSD Box archive; the server still requires `AESO_API_KEY` at startup, and
  redirects are re-validated against the corresponding host allow-list
- `AESO_API_KEY` is an upstream credential, not MCP client authentication. HTTP clients use a
  separate configured static bearer token on non-loopback binds. That custom authentication is
  not OAuth and does not expose OAuth Protected Resource Metadata; the built-in listener does not
  terminate TLS, so external traffic must use a trusted TLS-terminating proxy
- Bounded date ranges, observation caps, HTTP timeouts, and selective retries
- Raw-series pagination plus compact server-side aggregation for long historical requests
- HTTP Host/Origin validation, bearer authentication required for non-loopback binds by default,
  an explicit insecure-remote override, per-client rate limits, global
  concurrency admission, request-body limits, and secret-free health/readiness probes
- Correlation IDs and request timing logs that omit authorization headers, bodies, and query data
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
