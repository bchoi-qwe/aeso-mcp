# SPDX-License-Identifier: MIT
"""Market data services (prices, load, generation, snapshot)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from aeso_mcp.config import Settings
from aeso_mcp.errors import AesoMcpError, AuthenticationError, QueryTooLargeError
from aeso_mcp.models.common import DatasetMetadata, DataStatus, ProviderName
from aeso_mcp.models.generation import (
    FuelMixComponent,
    GenerationRequest,
    GenerationResponse,
    GenerationSnapshot,
    LoadInterval,
    LoadRequest,
    LoadResponse,
)
from aeso_mcp.models.grid import MarketSnapshotResponse
from aeso_mcp.models.prices import (
    PoolPriceRequest,
    PoolPriceResponse,
    SystemMarginalPriceRequest,
    SystemMarginalPriceResponse,
)
from aeso_mcp.providers.base import AesoDataProvider
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.ttl import historical_ttl_s
from aeso_mcp.timeutil import chronological_instant, market_now, utc_now, validate_range

logger = logging.getLogger(__name__)

RENEWABLE = frozenset({"Wind", "Solar", "Hydro"})


class MarketService:
    """Deterministic market data retrieval with caching and bounds."""

    def __init__(
        self,
        provider: AesoDataProvider,
        settings: Settings,
        cache: AsyncTTLCache | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._cache = cache or AsyncTTLCache()

    async def get_pool_prices(self, request: PoolPriceRequest) -> PoolPriceResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_pool_price_days,
            label="pool price range",
        )
        key = ("pool_prices", start.isoformat(), end.isoformat())
        intervals, prov = await self._cache.get_or_set(
            key,
            lambda: self._provider.get_pool_prices(start, end),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        if len(intervals) > self._settings.max_price_observations:
            raise QueryTooLargeError(
                f"Pool price query returned {len(intervals)} observations; "
                f"maximum is {self._settings.max_price_observations}. Narrow the date range."
            )
        warnings: list[str] = []
        if not request.include_forecast:
            intervals = [
                i.model_copy(
                    update={
                        "forecast_pool_price_cad_per_mwh": None,
                    }
                )
                for i in intervals
            ]
        return PoolPriceResponse(
            intervals=intervals,
            metadata=_meta(
                dataset="Pool Price Report",
                prov=prov,
                status=DataStatus.ACTUAL,
                units={"pool_price_cad_per_mwh": "CAD/MWh"},
                granularity="1h",
                start=start,
                end=end,
                count=len(intervals),
            ),
            warnings=warnings,
        )

    async def get_system_marginal_prices(
        self,
        request: SystemMarginalPriceRequest,
    ) -> SystemMarginalPriceResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_smp_days,
            label="system marginal price range",
        )
        key = ("smp", start.isoformat(), end.isoformat())
        intervals, prov = await self._cache.get_or_set(
            key,
            lambda: self._provider.get_system_marginal_prices(start, end),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        if len(intervals) > self._settings.max_smp_observations:
            raise QueryTooLargeError(
                f"SMP query returned {len(intervals)} observations; "
                f"maximum is {self._settings.max_smp_observations}. "
                f"Narrow the range (max {self._settings.max_smp_days} days) or ask for pool prices."
            )
        return SystemMarginalPriceResponse(
            intervals=intervals,
            metadata=_meta(
                dataset="System Marginal Price Report",
                prov=prov,
                status=DataStatus.ACTUAL,
                units={"system_marginal_price_cad_per_mwh": "CAD/MWh"},
                granularity="variable (minute-level)",
                start=start,
                end=end,
                count=len(intervals),
            ),
        )

    async def get_load(self, request: LoadRequest) -> LoadResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_load_days,
            label="load range",
        )
        key = ("load", start.isoformat(), end.isoformat(), request.include_forecast)
        rows, prov = await self._cache.get_or_set(
            key,
            lambda: self._provider.get_load(start, end, include_forecast=request.include_forecast),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        intervals = [
            LoadInterval(
                interval_start=row["interval_start"],  # type: ignore[arg-type]
                interval_end=row.get("interval_end"),  # type: ignore[arg-type]
                load_mw=float(row["load_mw"]),  # type: ignore[arg-type]
                load_forecast_mw=(
                    float(row["load_forecast_mw"])  # type: ignore[arg-type]
                    if row.get("load_forecast_mw") is not None
                    else None
                ),
            )
            for row in rows
        ]
        return LoadResponse(
            intervals=intervals,
            metadata=_meta(
                dataset="Alberta Internal Load",
                prov=prov,
                status=DataStatus.ACTUAL,
                units={"load_mw": "MW", "load_forecast_mw": "MW"},
                granularity="1h",
                start=start,
                end=end,
                count=len(intervals),
            ),
        )

    async def get_generation(self, request: GenerationRequest) -> GenerationResponse:
        warnings: list[str] = []
        if request.start is None and request.end is None:
            key = ("fuel_mix",)
            observed_at, components, prov = await self._cache.get_or_set(
                key,
                lambda: self._provider.get_fuel_mix(),
                ttl_s=self._settings.cache_ttl_snapshot_s,
            )
            total = sum(c.generation_mw for c in components)
            renewable = sum(c.generation_mw for c in components if c.fuel_type in RENEWABLE)
            snapshot = GenerationSnapshot(
                observed_at=observed_at,
                components=components,
                total_generation_mw=total,
                renewable_generation_mw=renewable,
                renewable_share=(renewable / total) if total else 0.0,
            )
            return GenerationResponse(
                snapshot=snapshot,
                metadata=_meta(
                    dataset="Current Fuel Mix",
                    prov=prov,
                    status=DataStatus.ACTUAL,
                    units={"generation_mw": "MW"},
                    granularity="current",
                    count=len(components),
                ),
            )

        if request.start is None or request.end is None:
            from aeso_mcp.errors import InvalidDateRangeError

            raise InvalidDateRangeError(
                "Provide both start and end for historical generation, or omit both for current fuel mix."
            )

        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_load_days,
            label="generation range",
        )
        intervals, prov = await self._cache.get_or_set(
            ("generation_history", start.isoformat(), end.isoformat()),
            lambda: self._provider.get_generation_history(start, end),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        warnings.append(
            "Historical generation currently includes wind and solar only; "
            "full fuel-mix history is not available from the public CSD endpoint."
        )
        return GenerationResponse(
            intervals=intervals,
            metadata=_meta(
                dataset="Wind/Solar Generation",
                prov=prov,
                status=DataStatus.ACTUAL,
                units={"generation_mw": "MW"},
                granularity="1h",
                start=start,
                end=end,
                count=len(intervals),
            ),
            warnings=warnings,
        )

    async def get_market_snapshot(self) -> MarketSnapshotResponse:
        key = ("market_snapshot",)
        payload = await self._cache.get_or_set(
            key,
            self._build_snapshot,
            ttl_s=self._settings.cache_ttl_snapshot_s,
        )
        return payload

    async def _build_snapshot(self) -> MarketSnapshotResponse:
        warnings: list[str] = []
        observed_at, csd, prov = await self._provider.get_supply_demand_snapshot()
        components: list[FuelMixComponent] = csd["generation_by_fuel"]  # type: ignore[assignment]
        reserves: dict[str, float | None] = csd["reserves"]  # type: ignore[assignment]

        pool_price = None
        smp = None
        now = market_now()
        try:
            prices, _ = await self._provider.get_pool_prices(
                now - timedelta(hours=6), now + timedelta(hours=1)
            )
            if prices:
                pool_price = max(
                    prices, key=lambda i: chronological_instant(i.interval_start)
                ).pool_price_cad_per_mwh
        except AuthenticationError:
            raise
        except AesoMcpError:
            warnings.append("Recent pool price unavailable for snapshot.")
            logger.warning("snapshot_pool_price_unavailable")

        try:
            smps, _ = await self._provider.get_system_marginal_prices(
                now - timedelta(hours=2), now + timedelta(hours=1)
            )
            if smps:
                smp = max(
                    smps, key=lambda i: chronological_instant(i.interval_start)
                ).system_marginal_price_cad_per_mwh
        except AuthenticationError:
            raise
        except AesoMcpError:
            warnings.append("Recent system marginal price unavailable for snapshot.")
            logger.warning("snapshot_smp_unavailable")

        total = _opt_float(csd.get("total_generation_mw"))
        if total is None:
            total = sum(c.generation_mw for c in components)
        wind = next((c.generation_mw for c in components if c.fuel_type == "Wind"), None)
        solar = next((c.generation_mw for c in components if c.fuel_type == "Solar"), None)
        renewable = sum(c.generation_mw for c in components if c.fuel_type in RENEWABLE)
        ail = _opt_float(csd.get("alberta_internal_load_mw"))

        status = DataStatus.ACTUAL
        if pool_price is None or ail is None:
            status = DataStatus.PRELIMINARY
            if pool_price is None:
                warnings.append("Snapshot is preliminary: recent pool price missing.")
            if ail is None:
                warnings.append("Snapshot is preliminary: Alberta Internal Load missing.")

        return MarketSnapshotResponse(
            observed_at=observed_at,
            pool_price_cad_per_mwh=pool_price,
            system_marginal_price_cad_per_mwh=smp,
            alberta_internal_load_mw=ail,
            total_generation_mw=total,
            generation_by_fuel=components,
            wind_generation_mw=wind,
            solar_generation_mw=solar,
            renewable_share=(renewable / total) if total else None,
            net_interchange_mw=_opt_float(csd.get("net_interchange_mw")),
            interchange_paths=csd.get("interchange_paths") or [],  # type: ignore[arg-type]
            contingency_reserve_required_mw=reserves.get("contingency_reserve_required_mw"),
            dispatched_contingency_reserve_total_mw=reserves.get(
                "dispatched_contingency_reserve_total_mw"
            ),
            metadata=_meta(
                dataset="Market Snapshot",
                prov=prov,
                status=status,
                units={
                    "pool_price_cad_per_mwh": "CAD/MWh",
                    "system_marginal_price_cad_per_mwh": "CAD/MWh",
                    "alberta_internal_load_mw": "MW",
                    "generation_mw": "MW",
                    "net_interchange_mw": "MW",
                },
                granularity="current",
            ),
            warnings=warnings,
        )


def _meta(
    *,
    dataset: str,
    prov: dict[str, str],
    status: DataStatus,
    units: dict[str, str],
    granularity: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    count: int | None = None,
) -> DatasetMetadata:
    provider = ProviderName(prov.get("provider", ProviderName.GRIDSTATUS.value))
    return DatasetMetadata(
        dataset=dataset,
        source_product=prov.get("source_product"),
        api_version=prov.get("api_version"),
        retrieved_at=utc_now(),
        status=status,
        units=units,
        observation_granularity=granularity,
        request_start=start,
        request_end=end,
        provider=provider,
        observation_count=count,
    )


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Re-export intentionally kept small
__all__ = ["MarketService"]
