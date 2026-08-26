# SPDX-License-Identifier: MIT
"""Operating-reserve public reports and deterministic summaries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta
from statistics import mean

from aeso_mcp.config import Settings
from aeso_mcp.errors import InvalidDateRangeError
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DataStatus,
    FinalityStatus,
    ObservationType,
    ProviderName,
)
from aeso_mcp.models.operations import PageInfo
from aeso_mcp.models.reserves import (
    OperatingReserveActivationInterval,
    OperatingReserveActivationsResponse,
    OperatingReserveDateRangeRequest,
    OperatingReserveForecastRequest,
    OperatingReserveForecastResponse,
    OperatingReservePriceInterval,
    OperatingReservePricesResponse,
    OperatingReserveSummaryRequest,
    OperatingReserveSummaryResponse,
    OperatingReserveSummaryResult,
)
from aeso_mcp.providers.public_reports import AesoPublicReportsProvider
from aeso_mcp.services.cache import AsyncTTLCache, CacheInfo
from aeso_mcp.timeutil import start_of_market_day, utc_now


class OperatingReserveService:
    """Normalize the active/standby reserve market without conflating price concepts."""

    def __init__(
        self,
        provider: AesoPublicReportsProvider,
        settings: Settings,
        cache: AsyncTTLCache,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._cache = cache

    async def get_prices(
        self,
        request: OperatingReserveDateRangeRequest,
        *,
        paginate: bool = True,
    ) -> OperatingReservePricesResponse:
        _validate_range(request.start_date, request.end_date, max_days=366)
        cached = await self._cache.get_or_set_with_metadata(
            ("or_prices", request.start_date.isoformat(), request.end_date.isoformat()),
            lambda: self._provider.get_operating_reserve_prices(
                request.start_date, request.end_date
            ),
            ttl_s=self._settings.cache_ttl_historical_public_report_s,
        )
        intervals, publication_time, provenance = cached.value
        filters = set(request.reserve_types)
        if filters:
            intervals = [item for item in intervals if item.reserve_type in filters]
        values, page = _page_or_all(intervals, request.offset, request.limit, paginate=paginate)
        return OperatingReservePricesResponse(
            intervals=values,
            page=page,
            metadata=_metadata(
                "Operating Reserve Prices",
                provenance,
                cached.info,
                count=len(intervals),
                completeness=_completeness(intervals),
                granularity="daily product/time-block",
                start=request.start_date,
                end=request.end_date,
                publication_time=publication_time,
                units={
                    "active_price_cad_per_mw": "CAD/MW",
                    "premium_price_cad_per_mw": "CAD/MW",
                    "activation_price_cad_per_mwh": "CAD/MWh",
                    "clearing_blended_price_cad_per_mw": "CAD/MW",
                    "volume_mw": "MW",
                },
            ),
        )

    async def get_forecast(
        self, request: OperatingReserveForecastRequest
    ) -> OperatingReserveForecastResponse:
        cached = await self._cache.get_or_set_with_metadata(
            ("or_forecast",),
            self._provider.get_operating_reserve_forecast,
            ttl_s=self._settings.cache_ttl_forecast_s,
        )
        intervals, publication_time, provenance = cached.value
        filters = set(request.reserve_types)
        if filters:
            intervals = [
                item.model_copy(
                    update={
                        f"{procurement}_{reserve_type}_mw": None
                        for procurement in ("active", "standby")
                        for reserve_type in ("regulating", "spinning", "supplemental")
                        if reserve_type not in filters
                    }
                )
                for item in intervals
            ]
        values, page = _page_or_all(intervals, request.offset, request.limit, paginate=True)
        return OperatingReserveForecastResponse(
            intervals=values,
            page=page,
            metadata=_metadata(
                "Operating Reserve Volume Forecast",
                provenance,
                cached.info,
                count=len(intervals),
                completeness=_completeness(intervals),
                granularity="1h",
                publication_time=publication_time,
                status=DataStatus.FORECAST,
                observation_type=ObservationType.FORECAST,
                finality=FinalityStatus.PRELIMINARY,
                units={"active_*_mw": "MW", "standby_*_mw": "MW"},
            ),
        )

    async def get_activations(
        self,
        request: OperatingReserveDateRangeRequest,
        *,
        paginate: bool = True,
    ) -> OperatingReserveActivationsResponse:
        _validate_range(request.start_date, request.end_date, max_days=366)
        cached = await self._cache.get_or_set_with_metadata(
            ("or_activations", request.start_date.isoformat(), request.end_date.isoformat()),
            lambda: self._provider.get_operating_reserve_activations(
                request.start_date, request.end_date
            ),
            ttl_s=self._settings.cache_ttl_historical_public_report_s,
        )
        intervals, publication_time, provenance = cached.value
        filters = set(request.reserve_types)
        if filters:
            intervals = [item for item in intervals if item.reserve_type in filters]
        values, page = _page_or_all(intervals, request.offset, request.limit, paginate=paginate)
        return OperatingReserveActivationsResponse(
            intervals=values,
            page=page,
            metadata=_metadata(
                "Operating Reserve Activations",
                provenance,
                cached.info,
                count=len(intervals),
                completeness=_completeness(intervals),
                granularity="hourly activation event",
                start=request.start_date,
                end=request.end_date,
                publication_time=publication_time,
                units={
                    "activated_volume_mw": "MW",
                    "weighted_average_activation_price_cad_per_mwh": "CAD/MWh",
                },
            ),
        )

    async def summarize(
        self, request: OperatingReserveSummaryRequest
    ) -> OperatingReserveSummaryResponse:
        base_request = OperatingReserveDateRangeRequest(
            start_date=request.start_date,
            end_date=request.end_date,
            reserve_types=request.reserve_types,
        )
        prices = await self.get_prices(base_request, paginate=False)
        activations = (
            await self.get_activations(base_request, paginate=False)
            if request.include_activations
            else None
        )
        price_groups: dict[tuple[str, str], list[OperatingReservePriceInterval]] = defaultdict(list)
        for item in prices.intervals:
            price_groups[(item.procurement, item.reserve_type)].append(item)
        activation_groups: dict[str, list[OperatingReserveActivationInterval]] = defaultdict(list)
        if activations is not None:
            for item in activations.intervals:
                activation_groups[item.reserve_type].append(item)
        results: list[OperatingReserveSummaryResult] = []
        for (procurement, reserve_type), rows in sorted(price_groups.items()):
            price_values = [
                value
                for item in rows
                for value in [
                    item.active_price_cad_per_mw
                    if procurement == "active"
                    else item.clearing_blended_price_cad_per_mw
                ]
                if value is not None
            ]
            volumes = [item.volume_mw for item in rows if item.volume_mw is not None]
            activation_rows = (
                activation_groups.get(reserve_type, []) if procurement == "standby" else []
            )
            activation_volume = sum(item.activated_volume_mw for item in activation_rows)
            activation_price = (
                sum(
                    item.activated_volume_mw * item.weighted_average_activation_price_cad_per_mwh
                    for item in activation_rows
                )
                / activation_volume
                if activation_volume
                else None
            )
            results.append(
                OperatingReserveSummaryResult(
                    procurement="active" if procurement == "active" else "standby",
                    reserve_type=(
                        "regulating"
                        if reserve_type == "regulating"
                        else "spinning"
                        if reserve_type == "spinning"
                        else "supplemental"
                    ),
                    observation_count=len(rows),
                    average_price_cad_per_mw=mean(price_values) if price_values else None,
                    minimum_price_cad_per_mw=min(price_values) if price_values else None,
                    maximum_price_cad_per_mw=max(price_values) if price_values else None,
                    average_volume_mw=mean(volumes) if volumes else None,
                    activated_volume_mw=activation_volume,
                    average_activation_price_cad_per_mwh=activation_price,
                )
            )
        warnings = list(prices.warnings)
        if activations is not None:
            warnings.extend(activations.warnings)
        return OperatingReserveSummaryResponse(
            results=results,
            methodology=(
                "Active products use the published active price. Standby products use the "
                "published clearing blended price; premium and activation strike prices remain "
                "separate in the raw price tool. Average activation price is volume-weighted."
            ),
            metadata=_metadata(
                "Operating Reserve Market Summary",
                {"source_product": "Derived from public operating-reserve reports"},
                None,
                count=len(results),
                completeness=_completeness(results),
                granularity="date-range product summary",
                start=request.start_date,
                end=request.end_date,
                provider=ProviderName.DERIVED,
                observation_type=ObservationType.DERIVED,
                units={
                    "price": "CAD/MW",
                    "activation_price": "CAD/MWh",
                    "volume": "MW",
                },
            ),
            warnings=list(dict.fromkeys(warnings)),
        )


def _validate_range(start, end, *, max_days: int) -> None:
    if end < start:
        raise InvalidDateRangeError("Operating-reserve end_date must be on or after start_date.")
    if (end - start).days + 1 > max_days:
        raise InvalidDateRangeError(
            f"Operating-reserve range cannot exceed {max_days} inclusive days."
        )


def _page_or_all[T](
    values: Sequence[T], offset: int, limit: int, *, paginate: bool
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
    return page, PageInfo(
        offset=offset,
        limit=limit,
        returned=len(page),
        total=total,
        next_offset=offset + len(page) if offset + len(page) < total else None,
    )


def _completeness(values: Sequence[object]) -> DataCompleteness:
    return DataCompleteness.COMPLETE if values else DataCompleteness.EMPTY


def _metadata(
    dataset: str,
    provenance: dict[str, str],
    cache_info: CacheInfo | None,
    *,
    count: int,
    completeness: DataCompleteness,
    granularity: str,
    units: dict[str, str],
    start=None,
    end=None,
    publication_time=None,
    provider: ProviderName = ProviderName.AESO_PUBLIC_REPORT,
    status: DataStatus = DataStatus.ACTUAL,
    observation_type: ObservationType = ObservationType.ACTUAL,
    finality: FinalityStatus = FinalityStatus.UNKNOWN,
) -> DatasetMetadata:
    retrieved = cache_info.retrieved_at if cache_info else utc_now()
    served = cache_info.served_at if cache_info else utc_now()
    return DatasetMetadata(
        dataset=dataset,
        source_product=provenance.get("source_product"),
        retrieved_at=retrieved,
        served_at=served,
        cache_hit=cache_info.cache_hit if cache_info else False,
        cache_age=cache_info.cache_age if cache_info else None,
        status=status,
        observation_type=observation_type,
        finality=finality,
        completeness=completeness,
        available_series=[dataset] if count else [],
        units=units,
        observation_granularity=granularity,
        request_start=start_of_market_day(start) if start else None,
        request_end=start_of_market_day(end + timedelta(days=1)) if end else None,
        publication_time=publication_time,
        provider=provider,
        observation_count=count,
    )
