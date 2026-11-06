# SPDX-License-Identifier: MIT
"""Cache ownership, shared reuse, and configured bounds at service boundaries."""

from unittest.mock import AsyncMock

from pydantic import SecretStr

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.models.assets import AssetsRequest
from aeso_mcp.services.assets import AssetsService
from aeso_mcp.services.cache import AsyncTTLCache


async def test_container_services_retain_empty_shared_cache() -> None:
    container = build_container(Settings(aeso_api_key=SecretStr("test-key")))
    try:
        for name in (
            "market",
            "grid",
            "assets",
            "forecasts",
            "reports",
            "research_data",
            "market_power",
            "transmission",
            "operations",
            "reserves",
        ):
            assert getattr(container, name)._cache is container.cache, name
    finally:
        await container.aclose()


async def test_shared_cache_reuses_fetch_and_clear_invalidates_services() -> None:
    settings = Settings(aeso_api_key=SecretStr("test-key"))
    provider = AsyncMock()
    provider.get_assets.return_value = ([], {})
    cache = AsyncTTLCache()
    first = AssetsService(provider, settings, cache)
    second = AssetsService(provider, settings, cache)
    await first.get_assets(AssetsRequest())
    cached = await second.get_assets(AssetsRequest())
    provider.get_assets.assert_awaited_once()
    assert cached.metadata.cache_hit
    cache.clear()
    await second.get_assets(AssetsRequest())
    assert provider.get_assets.await_count == 2


async def test_default_service_cache_respects_configured_capacity() -> None:
    settings = Settings(aeso_api_key=SecretStr("test-key"), cache_max_entries=16)
    provider = AsyncMock()
    provider.get_assets.return_value = ([], {})
    service = AssetsService(provider, settings)
    for index in range(17):
        await service.get_assets(AssetsRequest(asset_id=str(index)))
    await service.get_assets(AssetsRequest(asset_id="0"))
    assert provider.get_assets.await_count == 18
