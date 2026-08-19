# SPDX-License-Identifier: MIT
"""Market data services (prices, load, generation, snapshot)."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from aeso_mcp.config import Settings
from aeso_mcp.errors import AesoMcpError, AuthenticationError, QueryTooLargeError
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DataStatus,
    FinalityStatus,
    ObservationType,
    ProviderName,
)
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
from aeso_mcp.models.operations import PageInfo
from aeso_mcp.models.prices import (
    PoolPriceRequest,
    PoolPriceResponse,
    SystemMarginalPriceRequest,
    SystemMarginalPriceResponse,
)
from aeso_mcp.providers.base import AesoDataProvider
from aeso_mcp.services.cache import AsyncTTLCache, CacheInfo
from aeso_mcp.services.ttl import historical_ttl_s
from aeso_mcp.timeutil import chronological_instant, market_now, to_utc, utc_now, validate_range

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

    async def get_pool_prices(
        self,
        request: PoolPriceRequest,
        *,
        paginate: bool = True,
    ) -> PoolPriceResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_pool_price_days,
            label="pool price range",
        )
        key = ("pool_prices", start.isoformat(), end.isoformat())
        cached = await self._cache.get_or_set_with_metadata(
            key,
            lambda: self._provider.get_pool_prices(start, end),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        all_intervals, prov = cached.value
        if len(all_intervals) > self._settings.max_price_observations:
            raise QueryTooLargeError(
                f"Pool price query returned {len(all_intervals)} observations; "
                f"maximum is {self._settings.max_price_observations}. Narrow the date range."
            )
        warnings: list[str] = []
        available_series = _series(prov, "available_series", ["pool_price"])
        missing_series = _series(prov, "missing_series")
        if request.include_forecast:
            if any(i.forecast_pool_price_cad_per_mwh is not None for i in all_intervals):
                if "pool_price_forecast" not in available_series:
                    available_series.append("pool_price_forecast")
            elif "pool_price_forecast" not in missing_series:
                missing_series.append("pool_price_forecast")
                warnings.append(
                    "Pool price forecast was requested but was not available for this range."
                )
        if not request.include_forecast:
            all_intervals = [
                i.model_copy(
                    update={
                        "forecast_pool_price_cad_per_mwh": None,
                    }
                )
                for i in all_intervals
            ]
        all_intervals.sort(key=lambda i: chronological_instant(i.interval_start))
        total_count = len(all_intervals)
        intervals, page = _paginate(all_intervals, request.offset, request.limit, paginate)
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
                count=total_count,
                cache_info=cached.info,
                available_series=available_series,
                missing_series=missing_series,
                expected_observations=_expected_hourly_observations(start, end),
            ),
            page=page,
            warnings=warnings,
        )

    async def get_system_marginal_prices(
        self,
        request: SystemMarginalPriceRequest,
        *,
        paginate: bool = True,
    ) -> SystemMarginalPriceResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_smp_days,
            label="system marginal price range",
        )
        key = ("smp", start.isoformat(), end.isoformat())
        cached = await self._cache.get_or_set_with_metadata(
            key,
            lambda: self._provider.get_system_marginal_prices(start, end),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        all_intervals, prov = cached.value
        if len(all_intervals) > self._settings.max_smp_observations:
            raise QueryTooLargeError(
                f"SMP query returned {len(all_intervals)} observations; "
                f"maximum is {self._settings.max_smp_observations}. "
                f"Narrow the range (max {self._settings.max_smp_days} days) or ask for pool prices."
            )
        all_intervals.sort(key=lambda i: chronological_instant(i.interval_start))
        total_count = len(all_intervals)
        intervals, page = _paginate(all_intervals, request.offset, request.limit, paginate)
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
                count=total_count,
                cache_info=cached.info,
                available_series=["smp"] if total_count else [],
                completeness=(DataCompleteness.COMPLETE if total_count else DataCompleteness.EMPTY),
            ),
            page=page,
        )

    async def get_load(
        self,
        request: LoadRequest,
        *,
        paginate: bool = True,
    ) -> LoadResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_load_days,
            label="load range",
        )
        key = ("load", start.isoformat(), end.isoformat(), request.include_forecast)
        cached = await self._cache.get_or_set_with_metadata(
            key,
            lambda: self._provider.get_load(start, end, include_forecast=request.include_forecast),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        rows, prov = cached.value
        all_intervals = [
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
        warnings: list[str] = []
        available_series = _series(prov, "available_series", ["load"])
        missing_series = _series(prov, "missing_series")
        if request.include_forecast and "load_forecast" in missing_series:
            warnings.append(
                "Load forecast was requested but is unavailable; actual load observations are returned."
            )
        all_intervals.sort(key=lambda i: chronological_instant(i.interval_start))
        total_count = len(all_intervals)
        intervals, page = _paginate(all_intervals, request.offset, request.limit, paginate)
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
                count=total_count,
                cache_info=cached.info,
                available_series=available_series,
                missing_series=missing_series,
                expected_observations=_expected_hourly_observations(start, end),
            ),
            page=page,
            warnings=warnings,
        )

    async def get_generation(
        self,
        request: GenerationRequest,
        *,
        paginate: bool = True,
    ) -> GenerationResponse:
        warnings: list[str] = []
        if request.start is None and request.end is None:
            key = ("fuel_mix",)
            cached = await self._cache.get_or_set_with_metadata(
                key,
                lambda: self._provider.get_fuel_mix(),
                ttl_s=self._settings.cache_ttl_snapshot_s,
            )
            observed_at, components, prov = cached.value
            total = sum(c.generation_mw for c in components)
            renewable = sum(c.generation_mw for c in components if c.fuel_type in RENEWABLE)
            page_components, page = _paginate(
                components,
                request.offset,
                request.limit,
                paginate=paginate,
            )
            snapshot = GenerationSnapshot(
                observed_at=observed_at,
                components=page_components,
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
                    cache_info=cached.info,
                    available_series=[c.fuel_type for c in components],
                    completeness=(
                        DataCompleteness.COMPLETE if components else DataCompleteness.EMPTY
                    ),
                ),
                page=page,
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
        cached = await self._cache.get_or_set_with_metadata(
            ("generation_history", start.isoformat(), end.isoformat()),
            lambda: self._provider.get_generation_history(start, end),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        all_intervals, prov = cached.value
        available_series = _series(prov, "available_series")
        missing_series = _series(prov, "missing_series")
        if missing_series:
            warnings.append(
                "Historical generation is partial: unavailable series: "
                + ", ".join(missing_series)
                + "."
            )
        expected_series_count = len(set(available_series + missing_series)) or 2
        warnings.append(
            "Historical generation currently includes wind and solar only; "
            "full fuel-mix history is not available from the public CSD endpoint."
        )
        all_intervals.sort(
            key=lambda interval: (
                chronological_instant(interval.interval_start),
                interval.fuel_type,
            )
        )
        total_count = len(all_intervals)
        intervals, page = _paginate(all_intervals, request.offset, request.limit, paginate)
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
                count=total_count,
                cache_info=cached.info,
                available_series=available_series,
                missing_series=missing_series,
                expected_observations=_expected_hourly_observations(start, end)
                * expected_series_count,
            ),
            page=page,
            warnings=warnings,
        )

    async def get_market_snapshot(self) -> MarketSnapshotResponse:
        key = ("market_snapshot",)
        cached = await self._cache.get_or_set_with_metadata(
            key,
            self._build_snapshot,
            ttl_s=self._settings.cache_ttl_snapshot_s,
        )
        payload = cached.value
        return payload.model_copy(
            update={
                "metadata": payload.metadata.model_copy(
                    update={
                        "served_at": cached.info.served_at,
                        "cache_hit": cached.info.cache_hit,
                        "cache_age": cached.info.cache_age,
                    }
                )
            }
        )

    async def _build_snapshot(self) -> MarketSnapshotResponse:
        warnings: list[str] = []
        observed_at, csd, prov = await self._provider.get_supply_demand_snapshot()
        fetched_at = utc_now()
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
        missing_series: list[str] = []
        available_series = [c.fuel_type for c in components]
        if wind is None:
            missing_series.append("Wind")
            warnings.append("Snapshot is partial: Wind generation is missing from the CSD payload.")
        if solar is None:
            missing_series.append("Solar")
            warnings.append(
                "Snapshot is partial: Solar generation is missing from the CSD payload."
            )
        if pool_price is None or ail is None:
            status = DataStatus.PRELIMINARY
            if pool_price is None:
                warnings.append("Snapshot is preliminary: recent pool price missing.")
            if ail is None:
                warnings.append("Snapshot is preliminary: Alberta Internal Load missing.")
        if pool_price is None:
            missing_series.append("pool_price")
        else:
            available_series.append("pool_price")
        if smp is None:
            missing_series.append("smp")
        else:
            available_series.append("smp")
        if ail is None:
            missing_series.append("load")
        else:
            available_series.append("load")

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
                retrieved_at=fetched_at,
                available_series=available_series,
                missing_series=missing_series,
                completeness=(
                    DataCompleteness.PARTIAL if missing_series else DataCompleteness.COMPLETE
                ),
            ),
            warnings=warnings,
        )


def _meta(
    *,
    dataset: str,
    prov: Mapping[str, object],
    status: DataStatus,
    units: dict[str, str],
    granularity: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    publication_time: datetime | None = None,
    count: int | None = None,
    cache_info: CacheInfo | None = None,
    retrieved_at: datetime | None = None,
    served_at: datetime | None = None,
    observation_type: ObservationType = ObservationType.ACTUAL,
    finality: FinalityStatus | None = None,
    completeness: DataCompleteness | None = None,
    available_series: Sequence[str] | None = None,
    missing_series: Sequence[str] | None = None,
    expected_observations: int | None = None,
) -> DatasetMetadata:
    provider = ProviderName(str(prov.get("provider", ProviderName.GRIDSTATUS.value)))
    actual_retrieved_at = retrieved_at or (cache_info.retrieved_at if cache_info else utc_now())
    actual_served_at = served_at or (cache_info.served_at if cache_info else utc_now())
    actual_cache_hit = cache_info.cache_hit if cache_info else False
    actual_cache_age = cache_info.cache_age if cache_info else None
    available = list(available_series or _series(prov, "available_series"))
    missing = list(missing_series or _series(prov, "missing_series"))
    expected = expected_observations
    missing_observations = None
    if expected is not None:
        missing_observations = max(expected - (count or 0), 0)
    if completeness is None:
        provider_completeness = str(prov.get("completeness", ""))
        provider_state = (
            DataCompleteness(provider_completeness)
            if provider_completeness in {item.value for item in DataCompleteness}
            else None
        )
        if expected is not None:
            completeness = (
                DataCompleteness.EMPTY
                if not count
                else provider_state
                if provider_state in {DataCompleteness.PARTIAL, DataCompleteness.DEGRADED}
                else DataCompleteness.PARTIAL
                if missing_observations
                else DataCompleteness.PARTIAL
                if missing
                else DataCompleteness.COMPLETE
            )
        elif provider_state is not None:
            completeness = provider_state
        else:
            completeness = (
                DataCompleteness.EMPTY
                if count == 0
                else DataCompleteness.PARTIAL
                if missing
                else DataCompleteness.UNKNOWN
            )
    if finality is None:
        finality = (
            FinalityStatus.PRELIMINARY
            if status == DataStatus.PRELIMINARY
            else FinalityStatus.FINAL
            if status == DataStatus.FINAL
            else FinalityStatus.UNKNOWN
        )
    known = {
        "provider",
        "source_product",
        "api_version",
        "available_series",
        "missing_series",
        "completeness",
    }
    return DatasetMetadata(
        dataset=dataset,
        source_product=_provider_text(prov, "source_product"),
        api_version=_provider_text(prov, "api_version"),
        retrieved_at=actual_retrieved_at,
        served_at=actual_served_at,
        cache_hit=actual_cache_hit,
        cache_age=actual_cache_age,
        status=status,
        observation_type=observation_type,
        finality=finality,
        completeness=completeness or DataCompleteness.UNKNOWN,
        available_series=available,
        missing_series=missing,
        expected_observations=expected,
        missing_observations=missing_observations,
        expected_observation_count=expected,
        missing_observation_count=missing_observations,
        units=units,
        observation_granularity=granularity,
        request_start=start,
        request_end=end,
        publication_time=publication_time,
        provider=provider,
        observation_count=count,
        extra={key: value for key, value in prov.items() if key not in known},
    )


def _provider_text(prov: Mapping[str, object], key: str) -> str | None:
    value = prov.get(key)
    return value if isinstance(value, str) else None


def _series(
    prov: Mapping[str, object],
    key: str,
    default: Sequence[str] | None = None,
) -> list[str]:
    value = prov.get(key)
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [item for item in value if isinstance(item, str)]
    return list(default or [])


def _expected_hourly_observations(start: datetime, end: datetime) -> int:
    """Return expected one-hour intervals using elapsed (UTC) time."""
    return max(1, round((to_utc(end) - to_utc(start)).total_seconds() / 3600))


def _paginate[T](
    values: Sequence[T],
    offset: int,
    limit: int,
    paginate: bool,
) -> tuple[list[T], PageInfo]:
    total = len(values)
    if not paginate:
        return list(values), PageInfo(
            offset=0,
            limit=max(total, 1),
            returned=total,
            total=total,
            next_offset=None,
        )
    page = list(values[offset : offset + limit])
    next_offset = offset + len(page) if offset + len(page) < total else None
    return page, PageInfo(
        offset=offset,
        limit=limit,
        returned=len(page),
        total=total,
        next_offset=next_offset,
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
