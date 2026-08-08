# SPDX-License-Identifier: MIT
"""Application container wiring providers and services."""

from __future__ import annotations

from dataclasses import dataclass

from aeso_mcp.config import Settings
from aeso_mcp.providers.gridstatus import GridStatusProvider
from aeso_mcp.providers.http import AesoHttpClient
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
    apim_http: AesoHttpClient
    public_reports_http: AesoPublicReportsHttpClient

    async def aclose(self) -> None:
        """Close outbound HTTP clients (idempotent)."""
        for client in (self.public_reports_http, self.apim_http):
            close = getattr(client, "aclose", None)
            if close is None:
                continue
            await close()


def build_container(settings: Settings) -> AppContainer:
    """Construct the default production dependency graph."""
    cache = AsyncTTLCache(max_entries=settings.cache_max_entries)
    apim_http = AesoHttpClient(settings)
    provider = GridStatusProvider(settings, apim_http=apim_http)
    public_http = AesoPublicReportsHttpClient(settings)
    public_reports = AesoPublicReportsProvider(public_http)
    market = MarketService(provider, settings, cache)
    grid = GridService(provider, settings, cache)
    assets = AssetsService(provider, settings, cache)
    analytics = AnalyticsService(market, settings)
    transmission = TransmissionService(
        approved_provider=public_reports,
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
        apim_http=apim_http,
        public_reports_http=public_http,
    )
