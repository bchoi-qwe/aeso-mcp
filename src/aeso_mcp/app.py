# SPDX-License-Identifier: MIT
"""Application container wiring providers and services."""

from __future__ import annotations

from dataclasses import dataclass

from aeso_mcp.config import Settings
from aeso_mcp.providers.gridstatus import GridStatusProvider
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.services.assets import AssetsService
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.grid import GridService
from aeso_mcp.services.market import MarketService


@dataclass
class AppContainer:
    """Shared runtime dependencies for MCP tools."""

    settings: Settings
    cache: AsyncTTLCache
    market: MarketService
    grid: GridService
    assets: AssetsService
    analytics: AnalyticsService


def build_container(settings: Settings) -> AppContainer:
    """Construct the default production dependency graph."""
    cache = AsyncTTLCache()
    provider = GridStatusProvider(settings)
    market = MarketService(provider, settings, cache)
    grid = GridService(provider, settings, cache)
    assets = AssetsService(provider, settings, cache)
    analytics = AnalyticsService(market, settings)
    return AppContainer(
        settings=settings,
        cache=cache,
        market=market,
        grid=grid,
        assets=assets,
        analytics=analytics,
    )
