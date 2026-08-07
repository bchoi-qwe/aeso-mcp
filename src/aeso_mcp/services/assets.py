# SPDX-License-Identifier: MIT
"""Asset registry service."""

from __future__ import annotations

from aeso_mcp.config import Settings
from aeso_mcp.models.assets import AssetsRequest, AssetsResponse
from aeso_mcp.models.common import DatasetMetadata, DataStatus, ProviderName
from aeso_mcp.providers.base import AesoDataProvider
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.timeutil import utc_now


class AssetsService:
    """AESO asset list retrieval with caching."""

    def __init__(
        self,
        provider: AesoDataProvider,
        settings: Settings,
        cache: AsyncTTLCache | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._cache = cache or AsyncTTLCache()

    async def get_assets(self, request: AssetsRequest) -> AssetsResponse:
        key = (
            "assets",
            request.asset_id,
            request.pool_participant_id,
            request.operating_status,
            request.asset_type,
        )
        assets, prov = await self._cache.get_or_set(
            key,
            lambda: self._provider.get_assets(
                asset_id=request.asset_id,
                pool_participant_id=request.pool_participant_id,
                operating_status=request.operating_status,
                asset_type=request.asset_type,
            ),
            ttl_s=self._settings.cache_ttl_assets_s,
        )
        truncated = len(assets) > request.limit
        if truncated:
            assets = assets[: request.limit]
        return AssetsResponse(
            assets=assets,
            truncated=truncated,
            metadata=DatasetMetadata(
                dataset="Asset List",
                source_product=prov.get("source_product"),
                api_version=prov.get("api_version"),
                retrieved_at=utc_now(),
                status=DataStatus.ACTUAL,
                units={},
                observation_granularity="catalog",
                provider=ProviderName(prov.get("provider", "gridstatus")),
                observation_count=len(assets),
            ),
            warnings=(["Result truncated to the requested limit."] if truncated else []),
        )
