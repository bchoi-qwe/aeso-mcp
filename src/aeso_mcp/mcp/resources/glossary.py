# SPDX-License-Identifier: MIT
"""Glossary resource fallback text."""

from __future__ import annotations

GLOSSARY_FALLBACK = """# AESO Market Glossary

Concise original definitions for terms used by this MCP server.

## AIES
Alberta Interconnected Electric System.

## AIL (Alberta Internal Load)
Alberta electricity demand measure, reported in MW.

## Pool Price
Hourly Alberta wholesale settlement price in CAD/MWh.

## System Marginal Price (SMP)
Finer-grained real-time price in CAD/MWh.

## Merit Order
Ranked offer stack used for dispatch.

## Operating Reserve
Capacity held for contingencies and frequency response.

## Maximum Capability
Maximum asset output in MW.

## Interchange / Interties
Power flows between Alberta and neighbors, in MW.

## Settlement Interval
Hourly Pool Price intervals with explicit interval_start/interval_end in America/Edmonton.

## Forecast vs Actual
Forecast values are projections, not settled actuals. See response metadata.status.
"""
