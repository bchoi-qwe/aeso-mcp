# SPDX-License-Identifier: MIT
"""Application container wiring providers and services."""

from __future__ import annotations

from dataclasses import dataclass

from aeso_mcp.config import Settings
from aeso_mcp.providers.gridstatus import GridStatusProvider
from aeso_mcp.providers.public_reports import AesoPublicReportsProvider
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.services.assets import AssetsService
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.grid import GridService
from aeso_mcp.services.market import MarketService
from aeso_mcp.services.market_power import MarketPowerService
from aeso_mcp.services.transmission import TransmissionService


@dataclass
class AppContainer:
    """Shared runtime dependencies for MCP tools."""

    settings: Settings
    cache: AsyncTTLCache
    market: MarketService
    grid: GridService
    assets: AssetsService
    analytics: AnalyticsService
    transmission: TransmissionService
    market_power: MarketPowerService
    public_reports_http: AesoPublicReportsHttpClient


def build_container(settings: Settings) -> AppContainer:
    """Construct the default production dependency graph."""
    cache = AsyncTTLCache(max_entries=settings.cache_max_entries)
    provider = GridStatusProvider(settings)
    public_http = AesoPublicReportsHttpClient(settings)
    public_reports = AesoPublicReportsProvider(public_http)
    market = MarketService(provider, settings, cache)
    grid = GridService(provider, settings, cache)
    assets = AssetsService(provider, settings, cache)
    analytics = AnalyticsService(market, settings)
    transmission = TransmissionService(
        approved_provider=provider,
        long_range_provider=public_reports,
        settings=settings,
        cache=cache,
    )
    market_power = MarketPowerService(public_reports, settings, cache)
    return AppContainer(
        settings=settings,
        cache=cache,
        market=market,
        grid=grid,
        assets=assets,
        analytics=analytics,
        transmission=transmission,
        market_power=market_power,
        public_reports_http=public_http,
    )
