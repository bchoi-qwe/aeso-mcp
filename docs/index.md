# AESO MCP

Typed Alberta electricity-market data and deterministic research tools for AI agents.

`aeso-mcp` exposes current conditions, operational reports, historical individual-asset
generation, operating-reserve reports, and server-side calculations through one Model Context
Protocol server. Every result carries source, timing, unit, finality, and completeness metadata.

The server is an independent open-source project and is not affiliated with or endorsed by the
Alberta Electric System Operator (AESO).

## What the server can answer

- What are current Pool Price, load, generation, interchange, and reserve conditions?
- How did a price event differ from a baseline window?
- What capture price did a wind, solar, or individual asset realize against hourly Pool Price?
- How did net load, generator outages, supply-stack offers, or intertie capability change?
- What did AESO publish for operating-reserve prices, volume forecasts, and activations?
- What individual-asset CSD generation did AESO publish at hourly or five-minute resolution?
- How did the official wind, solar, AIL, or Pool Price forecast compare with paired actuals?
- What official supply-adequacy, supply-surplus, FFR, DDS, TMR, or system-event evidence covers an event?
- What gross hourly Pool Price energy revenue follows from an asset's metered MWh?

## Design commitments

- **Typed and bounded:** public requests use Pydantic contracts, fixed source routes, pagination,
  and date-range limits.
- **Timezone-aware:** market times use `America/Edmonton`; canonical UTC instants preserve DST
  chronology.
- **Source-specific:** authenticated APIM, GridStatus, ETS public reports, and the official CSD
  Box archive have separate adapters.
- **Research-safe:** calculations return observed statistics and descriptive associations, not
  unsupported causal claims.
- **Secret-safe:** `AESO_API_KEY` is sent only to the authenticated APIM gateway and is never sent
  to ETS or Box.

Start with [Getting started](getting-started.md), copy a maintained
[client configuration](client-configs.md), inspect the [architecture](architecture.md) and
[MCP interoperability/security](mcp-interoperability.md), browse the [generated MCP catalog](generated/mcp-catalog.md),
or jump to the [investigation gallery](examples.md).
