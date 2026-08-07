# SPDX-License-Identifier: MIT
"""Provider protocol for AESO market data."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from aeso_mcp.models.assets import AssetRecord
from aeso_mcp.models.generation import FuelMixComponent, GenerationInterval
from aeso_mcp.models.grid import InterchangePathFlow, OutageRecord
from aeso_mcp.models.prices import PoolPriceInterval, SystemMarginalPriceInterval


@runtime_checkable
class AesoDataProvider(Protocol):
    """Async interface for AESO dataset retrieval used by domain services."""

    async def get_pool_prices(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[PoolPriceInterval], dict[str, str]]:
        """Return pool price intervals and provenance metadata fields."""
        ...

    async def get_system_marginal_prices(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[SystemMarginalPriceInterval], dict[str, str]]: ...

    async def get_load(
        self,
        start: datetime,
        end: datetime,
        *,
        include_forecast: bool = False,
    ) -> tuple[list[dict[str, object]], dict[str, str]]: ...

    async def get_fuel_mix(self) -> tuple[datetime, list[FuelMixComponent], dict[str, str]]: ...

    async def get_generation_history(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[GenerationInterval], dict[str, str]]: ...

    async def get_interchange(
        self,
    ) -> tuple[datetime, list[InterchangePathFlow], float, dict[str, str]]: ...

    async def get_reserves(self) -> tuple[datetime, dict[str, float | None], dict[str, str]]: ...

    async def get_supply_demand_snapshot(
        self,
    ) -> tuple[datetime, dict[str, object], dict[str, str]]:
        """Return a shared CSD payload for snapshot assembly."""
        ...

    async def get_assets(
        self,
        *,
        asset_id: str | None = None,
        pool_participant_id: str | None = None,
        operating_status: str | None = None,
        asset_type: str | None = None,
    ) -> tuple[list[AssetRecord], dict[str, str]]: ...

    async def get_outages(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[OutageRecord], dict[str, str]]: ...
