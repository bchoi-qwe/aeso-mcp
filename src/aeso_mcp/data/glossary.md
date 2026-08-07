# SPDX-License-Identifier: MIT
"""Static glossary resource content (original summaries, not AESO copyrighted text)."""

from __future__ import annotations

GLOSSARY_MARKDOWN = """# AESO Market Glossary

Concise original definitions for terms used by this MCP server. Official references:
[AESO](https://www.aeso.ca/) · [AESO Developer Portal](https://developer-apim.aeso.ca/)

## AIES
Alberta Interconnected Electric System — the interconnected transmission system and
associated generating facilities operated in Alberta.

## AIL (Alberta Internal Load)
Alberta Internal Load is the measure of electricity demand within Alberta commonly reported
by AESO. Values in this server are expressed in **MW**.

## Pool Price
The hourly Alberta wholesale electricity market settlement price, expressed in **CAD/MWh**.
Pool Price is the primary hourly price series for settlement analysis. Prefer
`get_pool_prices` for hourly history and `get_market_snapshot` for a recent value.

## System Marginal Price (SMP)
A finer-grained real-time price signal published by AESO, typically changing within the hour
as the marginal resource changes. Expressed in **CAD/MWh**. Prefer
`get_system_marginal_prices` for minute-level observations.

## Merit Order
The ranked stack of offers used to dispatch generation from lowest to highest offer price
(subject to system constraints). Merit-order datasets are not yet exposed in v0.1.

## Operating Reserve
Capacity held to respond to contingencies and frequency events. This server surfaces
current contingency reserve and related indicators from the Current Supply Demand product.

## Maximum Capability
The maximum output an asset can provide under stated conditions, in **MW**.

## Interchange / Interties
Electricity flowing between Alberta and neighboring jurisdictions (for example British
Columbia, Saskatchewan, Montana). Flows are reported in **MW**. Net interchange aggregates
path flows.

## TTC / ATC
Total Transfer Capability / Available Transfer Capability — transmission limits on interties.
Not exposed as dedicated tools in v0.1.

## Settlement Interval
AESO Pool Price is published for hourly settlement intervals. This server represents intervals
with explicit `interval_start` and `interval_end` timestamps in **America/Edmonton**.

## Forecast vs Actual
Forecast values are projections and must not be treated as settled actuals. Response metadata
includes an explicit `status` field (`actual`, `forecast`, `preliminary`, `final`, `unknown`).

## Data Finality
Public operational feeds may be preliminary. Do not assume values are final settlement
quantities unless an official AESO settlement product says so.
"""
