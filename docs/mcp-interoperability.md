# MCP interoperability and security

`aeso-mcp` is an AESO domain server, not an everything-server or general-purpose MCP gateway. It
exposes its typed market-data tools, prompts, and resources while leaving unrelated optional MCP
features unimplemented. Its advertised capabilities, runtime behavior, and CI checks are intended
to match that scope.

## Protocol support

| Client/server path | Behavior | Verification |
| --- | --- | --- |
| Modern MCP | Targets `2026-07-28`: stateless requests carry protocol version and client capabilities in per-request `_meta`; the HTTP transport mirrors required routing fields in headers. The server implements `server/discover` and stamps modern results with `resultType` and server identity metadata. | MCP conformance frozen requirements for `2026-07-28`; Inspector CLI 2.8.0 modern HTTP smoke |
| Legacy MCP | FastMCP negotiates the `2025-11-25` initialize/session protocol for older clients. | MCP conformance 0.1.16 active suite; Inspector CLI 2.8.0 legacy HTTP smoke; in-process and installed-wheel checks |
| stdio | The same server and full catalog are available. `AESO_API_KEY` is read from the environment; MCP OAuth is not used on stdio. | In-process and installed-wheel stdio checks |

The modern transport follows the [2026-07-28 base protocol](https://modelcontextprotocol.io/specification/2026-07-28/basic)
and [Streamable HTTP requirements](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http),
including per-request metadata, `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` validation,
Origin protection, and HTTP error handling. The `/mcp` HTTP endpoint accepts POST requests; legacy
clients use FastMCP's negotiated stateful protocol path.

The HTTP bearer token is this server's **custom static authentication**, not an OAuth access token.
A bearer token is required by default for non-loopback binds; host/origin checks, request bounds,
rate limits, and concurrency limits also apply. The built-in HTTP listener does not terminate TLS:
for traffic outside a trusted private network, terminate TLS at a trusted reverse proxy and do not
send bearer credentials over public plaintext HTTP. The server does not implement OAuth discovery
or Protected Resource Metadata. MCP authorization is optional; the [authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
requires OAuth-specific behavior when an implementation chooses OAuth. `AESO_API_KEY` is separate:
it authorizes upstream AESO APIM calls, is required at startup, and is never a client credential.

## Deliberate feature scope

The server does not register sample fixture components just to satisfy generic everything-server
tests. For example, it has no completion handler, resource templates, image/audio/embedded-resource
tool outputs, progress-reporting demo tool, or tool/prompt that requests additional client input via
MRTR. Its resources and prompts are domain-specific and discoverable through `resources/list` and
`prompts/list`. These absences are not claims that the corresponding optional capability is
implemented.

All 63 tools remain visible by default. The BM25 discovery experiment did not justify hiding the
catalog: it can reduce catalog text but adds calls and returns suggestions even for no-tool
questions. See [agent-use evaluation](evals.md#progressive-discovery-prototype).

## CI interoperability gates

CI pins independent MCP tools rather than relying only on FastMCP's in-process client:

- `@modelcontextprotocol/conformance@0.1.16` checks legacy active-suite behavior.
- `@modelcontextprotocol/conformance@0.2.0-alpha.11 --requirements 2026-07-28` runs the frozen,
  version-specific modern server requirements. The current stable conformance CLI predates that
  requirement set; the alpha is test infrastructure only, not a runtime dependency.
- `@modelcontextprotocol/inspector@2.8.0 --cli` checks modern and legacy HTTP tool discovery. The
  modern check runs strict schema portability analysis and fails on findings.

The [2025 baseline](https://github.com/bchoi-qwe/aeso-mcp/blob/main/conformance-baseline.yml) records unsupported generic fixture scenarios. The
[2026 baseline](https://github.com/bchoi-qwe/aeso-mcp/blob/main/conformance-2026-baseline.yml) uses check-level entries so successful checks in
the same scenario remain enforced. Entries account for unrelated fixture names and optional
features not exposed by this AESO server; a new unbaselined failure or a now-passing baseline entry
fails CI. Review these files when server capabilities change. Non-scored extension and pending
scenarios are reported by the official runner but do not affect its 2026 required-conformance exit
status.

## Registry metadata validation

`server.json` is checked for version consistency in CI. Before a manual Registry submission, the
official publisher can validate its schema and semantic rules without publishing:

```bash
brew install mcp-publisher
mcp-publisher validate server.json
```

Registry validation is distinct from publication. Do not run `mcp-publisher publish` as part of CI.
