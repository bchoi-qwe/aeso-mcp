# SPDX-License-Identifier: MIT
"""Application container wiring providers and services."""

from __future__ import annotations

from dataclasses import dataclass

from aeso_mcp.config import Settings
from aeso_mcp.providers.archive_http import AesoArchiveHttpClient
from aeso_mcp.providers.forecasts import AesoForecastProvider
from aeso_mcp.providers.gridstatus import GridStatusProvider
from aeso_mcp.providers.historical_generation import HistoricalGenerationProvider
from aeso_mcp.providers.http import AesoHttpClient
from aeso_mcp.providers.operations import AesoOperationsProvider
from aeso_mcp.providers.public_reports import AesoPublicReportsProvider
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient
from aeso_mcp.providers.reports import AesoReportsProvider
from aeso_mcp.services.analytics import AnalyticsService
from aeso_mcp.services.assets import AssetsService
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.forecasts import ForecastService
from aeso_mcp.services.grid import GridService
from aeso_mcp.services.history import HistoryService
from aeso_mcp.services.market import MarketService
from aeso_mcp.services.market_power import MarketPowerService
from aeso_mcp.services.operations import OperationsService
from aeso_mcp.services.reports import ReportsService
from aeso_mcp.services.research import ResearchService
from aeso_mcp.services.reserves import OperatingReserveService
from aeso_mcp.services.transmission import TransmissionService
from aeso_mcp.storage.history import HistoricalStore


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
    operations: OperationsService
    history: HistoryService
    forecasts: ForecastService
    reports: ReportsService
    research: ResearchService
    reserves: OperatingReserveService
    apim_http: AesoHttpClient
    public_reports_http: AesoPublicReportsHttpClient
    archive_http: AesoArchiveHttpClient
    historical_store: HistoricalStore

    async def aclose(self) -> None:
        """Close outbound HTTP clients (idempotent)."""
        for client in (self.archive_http, self.public_reports_http, self.apim_http):
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
    archive_http = AesoArchiveHttpClient(settings)
    public_reports = AesoPublicReportsProvider(public_http)
    forecast_provider = AesoForecastProvider(public_http)
    reports_provider = AesoReportsProvider(public_http)
    historical_generation = HistoricalGenerationProvider(archive_http)
    historical_store = HistoricalStore(settings.history_store_path)
    market = MarketService(provider, settings, cache)
    grid = GridService(provider, settings, cache)
    assets = AssetsService(provider, settings, cache)
    forecasts = ForecastService(
        forecast_provider,
        settings,
        cache,
        ail_provider=provider,
    )
    reports = ReportsService(reports_provider, settings, cache)
    transmission = TransmissionService(
        approved_provider=public_reports,
        long_range_provider=public_reports,
        settings=settings,
        cache=cache,
    )
    market_power = MarketPowerService(public_reports, settings, cache)
    operations_provider = AesoOperationsProvider(apim_http)
    operations = OperationsService(operations_provider, market, settings, cache, reports=reports)
    history = HistoryService(
        historical_generation,
        public_reports,
        market,
        settings,
        store=historical_store,
    )
    analytics = AnalyticsService(
        market,
        settings,
        operations=operations,
        history=history,
        forecasts=forecasts,
        reports=reports,
    )
    reserves = OperatingReserveService(public_reports, settings, cache)
    research = ResearchService(
        market,
        history,
        operations,
        grid,
        reserves,
        settings,
        forecasts=forecasts,
        reports=reports,
    )
    return AppContainer(
        settings=settings,
        cache=cache,
        market=market,
        grid=grid,
        assets=assets,
        analytics=analytics,
        transmission=transmission,
        market_power=market_power,
        operations=operations,
        history=history,
        forecasts=forecasts,
        reports=reports,
        research=research,
        reserves=reserves,
        apim_http=apim_http,
        public_reports_http=public_http,
        archive_http=archive_http,
        historical_store=historical_store,
    )
