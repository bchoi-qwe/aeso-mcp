# SPDX-License-Identifier: MIT
"""Provider protocol for AESO market data."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from aeso_mcp.models.assets import AssetRecord
from aeso_mcp.models.generation import FuelMixComponent, GenerationInterval
from aeso_mcp.models.grid import InterchangePathFlow, OutageRecord
from aeso_mcp.models.prices import PoolPriceInterval, SystemMarginalPriceInterval

type ProviderMetadata = dict[str, Any]


@runtime_checkable
class AesoDataProvider(Protocol):
    """Async interface for AESO dataset retrieval used by domain services."""

    async def get_pool_prices(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[PoolPriceInterval], ProviderMetadata]:
        """Return pool price intervals and provenance metadata fields."""
        ...

    async def get_system_marginal_prices(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[SystemMarginalPriceInterval], ProviderMetadata]: ...

    async def get_load(
        self,
        start: datetime,
        end: datetime,
        *,
        include_forecast: bool = False,
    ) -> tuple[list[dict[str, object]], ProviderMetadata]: ...

    async def get_fuel_mix(self) -> tuple[datetime, list[FuelMixComponent], ProviderMetadata]: ...

    async def get_generation_history(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[GenerationInterval], ProviderMetadata]: ...

    async def get_interchange(
        self,
    ) -> tuple[datetime, list[InterchangePathFlow], float, ProviderMetadata]: ...

    async def get_reserves(self) -> tuple[datetime, dict[str, float | None], ProviderMetadata]: ...

    async def get_supply_demand_snapshot(
        self,
    ) -> tuple[datetime, dict[str, object], ProviderMetadata]:
        """Return a shared CSD payload for snapshot assembly."""
        ...

    async def get_assets(
        self,
        *,
        asset_id: str | None = None,
        pool_participant_id: str | None = None,
        operating_status: str | None = None,
        asset_type: str | None = None,
    ) -> tuple[list[AssetRecord], ProviderMetadata]: ...

    async def get_outages(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[OutageRecord], ProviderMetadata]: ...
