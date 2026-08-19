# SPDX-License-Identifier: MIT
"""Asset registry service."""

from __future__ import annotations

from aeso_mcp.config import Settings
from aeso_mcp.models.assets import AssetsRequest, AssetsResponse
from aeso_mcp.models.common import DataCompleteness, DataStatus
from aeso_mcp.providers.base import AesoDataProvider
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.market import _meta


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
        cached = await self._cache.get_or_set_with_metadata(
            key,
            lambda: self._provider.get_assets(
                asset_id=request.asset_id,
                pool_participant_id=request.pool_participant_id,
                operating_status=request.operating_status,
                asset_type=request.asset_type,
            ),
            ttl_s=self._settings.cache_ttl_assets_s,
        )
        assets, prov = cached.value
        truncated = len(assets) > request.limit
        if truncated:
            assets = assets[: request.limit]
        return AssetsResponse(
            assets=assets,
            truncated=truncated,
            metadata=_meta(
                dataset="Asset List",
                prov=prov,
                status=DataStatus.ACTUAL,
                units={},
                granularity="catalog",
                count=len(assets),
                cache_info=cached.info,
                available_series=["assets"] if assets else [],
                completeness=DataCompleteness.COMPLETE,
            ),
            warnings=(["Result truncated to the requested limit."] if truncated else []),
        )
