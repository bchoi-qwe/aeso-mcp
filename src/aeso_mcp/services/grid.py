# SPDX-License-Identifier: MIT
"""Grid operations services (interchange, reserves, outages)."""

from __future__ import annotations

from aeso_mcp.config import Settings
from aeso_mcp.models.common import DatasetMetadata, DataStatus, ProviderName
from aeso_mcp.models.grid import (
    InterchangeResponse,
    OutagesRequest,
    OutagesResponse,
    ReservesResponse,
)
from aeso_mcp.providers.base import AesoDataProvider
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.ttl import historical_ttl_s
from aeso_mcp.timeutil import utc_now, validate_range


class GridService:
    """Interchange, reserves, and outage retrieval."""

    def __init__(
        self,
        provider: AesoDataProvider,
        settings: Settings,
        cache: AsyncTTLCache | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._cache = cache or AsyncTTLCache()

    async def get_interchange(self) -> InterchangeResponse:
        observed_at, paths, net, prov = await self._cache.get_or_set(
            ("interchange",),
            lambda: self._provider.get_interchange(),
            ttl_s=self._settings.cache_ttl_snapshot_s,
        )
        return InterchangeResponse(
            observed_at=observed_at,
            paths=paths,
            net_interchange_mw=net,
            metadata=DatasetMetadata(
                dataset="Interchange Flows",
                source_product=prov.get("source_product"),
                api_version=prov.get("api_version"),
                retrieved_at=utc_now(),
                status=DataStatus.ACTUAL,
                units={"flow_mw": "MW", "net_interchange_mw": "MW"},
                observation_granularity="current",
                provider=ProviderName(prov.get("provider", "gridstatus")),
                observation_count=len(paths),
            ),
        )

    async def get_reserves(self) -> ReservesResponse:
        observed_at, values, prov = await self._cache.get_or_set(
            ("reserves",),
            lambda: self._provider.get_reserves(),
            ttl_s=self._settings.cache_ttl_snapshot_s,
        )
        return ReservesResponse(
            observed_at=observed_at,
            contingency_reserve_required_mw=values.get("contingency_reserve_required_mw"),
            dispatched_contingency_reserve_total_mw=values.get(
                "dispatched_contingency_reserve_total_mw"
            ),
            dispatched_contingency_reserve_gen_mw=values.get(
                "dispatched_contingency_reserve_gen_mw"
            ),
            dispatched_contingency_reserve_other_mw=values.get(
                "dispatched_contingency_reserve_other_mw"
            ),
            fast_frequency_response_dispatched_mw=values.get(
                "fast_frequency_response_dispatched_mw"
            ),
            fast_frequency_response_offered_mw=values.get("fast_frequency_response_offered_mw"),
            long_lead_time_volume_mw=values.get("long_lead_time_volume_mw"),
            metadata=DatasetMetadata(
                dataset="Operating Reserves",
                source_product=prov.get("source_product"),
                api_version=prov.get("api_version"),
                retrieved_at=utc_now(),
                status=DataStatus.ACTUAL,
                units={"*_mw": "MW"},
                observation_granularity="current",
                provider=ProviderName(prov.get("provider", "gridstatus")),
            ),
        )

    async def get_outages(self, request: OutagesRequest) -> OutagesResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_load_days,
            label="outages range",
        )
        outages, prov = await self._cache.get_or_set(
            ("outages", start.isoformat(), end.isoformat()),
            lambda: self._provider.get_outages(start, end),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        warnings: list[str] = []
        if not outages:
            warnings.append(
                "No hourly generator outage capacity intervals returned for the requested range "
                "(empty upstream response or dataset unavailable)."
            )
        return OutagesResponse(
            outages=outages,
            metadata=DatasetMetadata(
                dataset="Generator Outage Capacity (by fuel)",
                source_product=prov.get("source_product"),
                api_version=prov.get("api_version"),
                retrieved_at=utc_now(),
                status=DataStatus.ACTUAL,
                units={
                    "total_outage_mw": "MW",
                    "mothball_outage_mw": "MW",
                },
                observation_granularity="1h",
                request_start=start,
                request_end=end,
                provider=ProviderName(prov.get("provider", "gridstatus")),
                observation_count=len(outages),
            ),
            warnings=warnings,
        )
