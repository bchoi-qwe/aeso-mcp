# SPDX-License-Identifier: MIT
"""Bounded operational-report retrieval and compact cross-dataset analytics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from statistics import mean, median
from typing import Any

from aeso_mcp.config import Settings
from aeso_mcp.errors import InvalidDateRangeError, QueryTooLargeError
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DataStatus,
    FinalityStatus,
    ObservationType,
    ProviderName,
)
from aeso_mcp.models.generation import LoadRequest
from aeso_mcp.models.operations import (
    DailyPageRequest,
    DateRangePageRequest,
    EnergyMeritOrderResponse,
    GenerationCapacityInterval,
    GenerationCapacityResponse,
    IntertieCapabilityRequest,
    IntertieCapabilityResponse,
    IntertieOutagesResponse,
    LoadOutageForecastResponse,
    MarketHistoryBucket,
    MarketHistorySummaryRequest,
    MarketHistorySummaryResponse,
    MeteredVolumeRequest,
    MeteredVolumeResponse,
    OperatingReserveOfferControlResponse,
    PageInfo,
    SupplyTightnessResponse,
    UnitCommitmentResponse,
)
from aeso_mcp.models.prices import PoolPriceRequest
from aeso_mcp.providers.capabilities import OperationalReportsProvider
from aeso_mcp.services.cache import AsyncTTLCache, CacheInfo
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import (
    add_elapsed,
    chronological_instant,
    end_of_market_day,
    market_now,
    start_of_market_day,
    to_market,
    utc_now,
    validate_range,
)

_ENERGY_MERIT_ORDER_FIRST_DATE = date(2009, 9, 1)
_UNIT_COMMITMENT_FIRST_DATE = date(2024, 7, 1)
_GENERATION_CAPACITY_FIRST_DATE = date(2011, 1, 1)
_LOAD_OUTAGE_FIRST_DATE = date(2013, 9, 22)
_INTERTIE_OUTAGE_FIRST_DATE = date(2020, 11, 9)
_METERED_VOLUME_FIRST_DATE = date(2000, 1, 1)
_OPERATING_RESERVE_FIRST_DATE = date(2012, 10, 4)


class OperationsService:
    """Authenticated operational reports with query bounds, caching, and typed metadata."""

    def __init__(
        self,
        provider: OperationalReportsProvider,
        market: MarketService,
        settings: Settings,
        cache: AsyncTTLCache,
    ) -> None:
        self._provider = provider
        self._market = market
        self._settings = settings
        self._cache = cache

    async def get_energy_merit_order(
        self, request: DailyPageRequest, *, paginate: bool = True
    ) -> EnergyMeritOrderResponse:
        cutoff = market_now().date() - timedelta(days=60)
        _validate_report_date(
            request.report_date,
            earliest=_ENERGY_MERIT_ORDER_FIRST_DATE,
            latest=cutoff,
            label="energy merit order report date",
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("energy_merit_order", request.report_date.isoformat()),
            lambda: self._provider.get_energy_merit_order(request.report_date),
            ttl_s=self._settings.cache_ttl_historical_s,
        )
        records, prov = cached.value
        records = sorted(
            records,
            key=lambda row: (
                chronological_instant(row.interval_start),
                row.block_price_cad_per_mwh or 0.0,
                row.asset_id or "",
                row.block_number or 0,
            ),
        )
        page, info = _page_or_all(records, request.offset, request.limit, paginate)
        return EnergyMeritOrderResponse(
            blocks=page,
            page=info,
            metadata=_metadata(
                "Energy Merit Order Report",
                prov,
                cached.info,
                count=len(records),
                status=DataStatus.FINAL,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.FINAL,
                units={
                    "block_price_cad_per_mwh": "CAD/MWh",
                    "from_mw": "MW",
                    "to_mw": "MW",
                    "block_size_mw": "MW",
                    "available_mw": "MW",
                    "dispatched_mw": "MW",
                },
                granularity="hourly merit-order snapshot",
                report_start=request.report_date,
                report_end=request.report_date,
            ),
        )

    async def get_unit_commitments(
        self, request: DateRangePageRequest, *, paginate: bool = True
    ) -> UnitCommitmentResponse:
        _validate_date_range(
            request.start_date,
            request.end_date,
            earliest=_UNIT_COMMITMENT_FIRST_DATE,
            max_days=31,
            label="unit commitment range",
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("unit_commitments", request.start_date.isoformat(), request.end_date.isoformat()),
            lambda: self._provider.get_unit_commitments(request.start_date, request.end_date),
            ttl_s=_date_range_ttl(self._settings, request.end_date),
        )
        records, prov = cached.value
        records = sorted(
            records,
            key=lambda row: _directive_sort_key(row, request.start_date),
        )
        page, info = _page_or_all(records, request.offset, request.limit, paginate)
        return UnitCommitmentResponse(
            directives=page,
            page=info,
            metadata=_metadata(
                "Unit Commitment Data",
                prov,
                cached.info,
                count=len(records),
                status=DataStatus.PRELIMINARY,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.PRELIMINARY,
                units={},
                granularity="directive",
                report_start=request.start_date,
                report_end=request.end_date,
            ),
        )

    async def get_generation_capacity(
        self, request: DateRangePageRequest, *, paginate: bool = True
    ) -> GenerationCapacityResponse:
        _validate_date_range(
            request.start_date,
            request.end_date,
            earliest=_GENERATION_CAPACITY_FIRST_DATE,
            max_days=31,
            label="generation capacity range",
            max_future_days=762,
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("generation_capacity", request.start_date.isoformat(), request.end_date.isoformat()),
            lambda: self._provider.get_generation_capacity(request.start_date, request.end_date),
            ttl_s=_date_range_ttl(self._settings, request.end_date),
        )
        records, prov = cached.value
        records = sorted(
            records,
            key=lambda row: (
                chronological_instant(row.interval_start),
                row.fuel_type,
                row.sub_fuel_type or "",
            ),
        )
        page, info = _page_or_all(records, request.offset, request.limit, paginate)
        contains_future = request.end_date >= market_now().date()
        return GenerationCapacityResponse(
            intervals=page,
            page=info,
            metadata=_metadata(
                "AIES Generation Capacity and Outages",
                prov,
                cached.info,
                count=len(records),
                status=DataStatus.FORECAST if contains_future else DataStatus.ACTUAL,
                observation_type=(
                    ObservationType.FORECAST if contains_future else ObservationType.ACTUAL
                ),
                finality=(
                    FinalityStatus.PRELIMINARY if contains_future else FinalityStatus.UNKNOWN
                ),
                units={
                    "maximum_capability_mw": "MW",
                    "mothball_outage_mw": "MW",
                    "operating_outage_mw": "MW",
                    "available_capability_mw": "MW",
                },
                granularity="1h by fuel class",
                report_start=request.start_date,
                report_end=request.end_date,
            ),
        )

    async def get_load_outage_forecast(
        self, request: DateRangePageRequest
    ) -> LoadOutageForecastResponse:
        _validate_date_range(
            request.start_date,
            request.end_date,
            earliest=_LOAD_OUTAGE_FIRST_DATE,
            max_days=31,
            label="load outage forecast range",
            max_future_days=762,
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("load_outage_forecast", request.start_date.isoformat(), request.end_date.isoformat()),
            lambda: self._provider.get_load_outage_forecast(request.start_date, request.end_date),
            ttl_s=_date_range_ttl(self._settings, request.end_date),
        )
        records, prov = cached.value
        records = sorted(records, key=lambda row: chronological_instant(row.interval_start))
        page, info = _page(records, request.offset, request.limit)
        return LoadOutageForecastResponse(
            intervals=page,
            page=info,
            metadata=_metadata(
                "Load Outage Forecast",
                prov,
                cached.info,
                count=len(records),
                status=DataStatus.FORECAST,
                observation_type=ObservationType.FORECAST,
                finality=FinalityStatus.PRELIMINARY,
                units={"load_outage_forecast_mw": "MW"},
                granularity="1h",
                report_start=request.start_date,
                report_end=request.end_date,
            ),
        )

    async def get_intertie_capability(
        self, request: IntertieCapabilityRequest, *, paginate: bool = True
    ) -> IntertieCapabilityResponse:
        _validate_date_range(
            request.start_date,
            request.end_date,
            max_days=100,
            label="intertie capability range",
        )
        cached = await self._cache.get_or_set_with_metadata(
            (
                "intertie_capability",
                request.start_date.isoformat(),
                request.end_date.isoformat(),
                request.start_hour_ending,
                request.end_hour_ending,
                request.include_versions,
            ),
            lambda: self._provider.get_intertie_capability(
                request.start_date,
                request.end_date,
                start_hour_ending=request.start_hour_ending,
                end_hour_ending=request.end_hour_ending,
                include_versions=request.include_versions,
            ),
            ttl_s=_date_range_ttl(self._settings, request.end_date),
        )
        records, prov = cached.value
        records = sorted(
            records,
            key=lambda row: (
                chronological_instant(row.interval_start),
                row.intertie,
                row.direction,
            ),
        )
        page, info = _page_or_all(records, request.offset, request.limit, paginate)
        return IntertieCapabilityResponse(
            intervals=page,
            page=info,
            metadata=_metadata(
                "Intertie Capability",
                prov,
                cached.info,
                count=len(records),
                status=DataStatus.PRELIMINARY,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.PRELIMINARY,
                units={
                    "available_transfer_capability_mw": "MW",
                    "total_transfer_capability_mw": "MW",
                    "transmission_reliability_margin_mw": "MW",
                    "system_reliability_margin_mw": "MW",
                    "allocation_reliability_margin_mw": "MW",
                    "gross_offer_mw": "MW",
                },
                granularity="1h by path and direction",
                report_start=request.start_date,
                report_end=request.end_date,
            ),
        )

    async def get_intertie_outages(
        self, request: DateRangePageRequest, *, paginate: bool = True
    ) -> IntertieOutagesResponse:
        _validate_date_range(
            request.start_date,
            request.end_date,
            earliest=_INTERTIE_OUTAGE_FIRST_DATE,
            max_days=397,
            label="intertie outage range",
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("intertie_outages", request.start_date.isoformat(), request.end_date.isoformat()),
            lambda: self._provider.get_intertie_outages(request.start_date, request.end_date),
            ttl_s=_date_range_ttl(self._settings, request.end_date),
        )
        records, prov = cached.value
        records = sorted(records, key=lambda row: chronological_instant(row.interval_start))
        page, info = _page_or_all(records, request.offset, request.limit, paginate)
        return IntertieOutagesResponse(
            outages=page,
            page=info,
            metadata=_metadata(
                "Intertie Capability Outages",
                prov,
                cached.info,
                count=len(records),
                status=DataStatus.PRELIMINARY,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.PRELIMINARY,
                units={},
                granularity="outage event",
                report_start=request.start_date,
                report_end=request.end_date,
            ),
        )

    async def get_metered_volumes(self, request: MeteredVolumeRequest) -> MeteredVolumeResponse:
        if request.asset_ids:
            max_days = min(366, max(1, 100_000 // (24 * len(request.asset_ids))))
        elif request.pool_participant_ids:
            max_days = 16
        else:
            max_days = 16
        _validate_date_range(
            request.start_date,
            request.end_date,
            earliest=_METERED_VOLUME_FIRST_DATE,
            latest=market_now().date(),
            max_days=max_days,
            label="metered volume range",
        )
        asset_ids = request.asset_ids
        participant_ids = request.pool_participant_ids
        cached = await self._cache.get_or_set_with_metadata(
            (
                "metered_volumes",
                request.start_date.isoformat(),
                request.end_date.isoformat(),
                tuple(asset_ids),
                tuple(participant_ids),
            ),
            lambda: self._provider.get_metered_volumes(
                request.start_date,
                request.end_date,
                asset_ids=asset_ids,
                pool_participant_ids=participant_ids,
            ),
            ttl_s=_date_range_ttl(self._settings, request.end_date),
        )
        records, prov = cached.value
        records = sorted(
            records,
            key=lambda row: (
                chronological_instant(row.interval_start),
                row.asset_id,
                row.pool_participant_id or "",
            ),
        )
        page, info = _page(records, request.offset, request.limit)
        warnings: list[str] = []
        possibly_truncated = len(records) >= 100_000
        if possibly_truncated:
            warnings.append(
                "The report reached AESO's approximate 100,000-record response limit and may be "
                "partial. Narrow the date range or identifier list."
            )
        return MeteredVolumeResponse(
            intervals=page,
            page=info,
            metadata=_metadata(
                "Metered Volume Report",
                prov,
                cached.info,
                count=len(records),
                status=DataStatus.ACTUAL,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.UNKNOWN,
                units={"metered_volume_mwh": "MWh"},
                granularity="1h by asset",
                report_start=request.start_date,
                report_end=request.end_date,
                completeness=(
                    DataCompleteness.PARTIAL if possibly_truncated else DataCompleteness.COMPLETE
                ),
                available_series=["metered_volume"] if records else [],
                missing_series=["additional_records"] if possibly_truncated else [],
            ),
            warnings=warnings,
        )

    async def get_operating_reserve_offer_control(
        self, request: DailyPageRequest, *, paginate: bool = True
    ) -> OperatingReserveOfferControlResponse:
        cutoff = market_now().date() - timedelta(days=60)
        _validate_report_date(
            request.report_date,
            earliest=_OPERATING_RESERVE_FIRST_DATE,
            latest=cutoff,
            label="operating reserve offer-control report date",
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("operating_reserve_offer_control", request.report_date.isoformat()),
            lambda: self._provider.get_operating_reserve_offer_control(request.report_date),
            ttl_s=self._settings.cache_ttl_historical_s,
        )
        records, prov = cached.value
        records = sorted(
            records,
            key=lambda row: (
                chronological_instant(row.interval_start),
                row.product or "",
                row.active_price_cad_per_mwh or 0.0,
                row.asset_id or "",
            ),
        )
        page, info = _page_or_all(records, request.offset, request.limit, paginate)
        return OperatingReserveOfferControlResponse(
            blocks=page,
            page=info,
            metadata=_metadata(
                "Operating Reserve Offer Control Report",
                prov,
                cached.info,
                count=len(records),
                status=DataStatus.FINAL,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.FINAL,
                units={
                    "volume_mw": "MW",
                    "active_price_cad_per_mwh": "CAD/MWh",
                    "premium_price_cad_per_mwh": "CAD/MWh",
                    "activation_price_cad_per_mwh": "CAD/MWh",
                },
                granularity="hourly merit-order snapshot",
                report_start=request.report_date,
                report_end=request.report_date,
            ),
        )

    async def summarize_market_history(
        self, request: MarketHistorySummaryRequest
    ) -> MarketHistorySummaryResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_pool_price_days,
            label="market history summary range",
        )
        estimated = _estimated_bucket_count(start, end, request.bucket)
        if estimated > 400:
            raise QueryTooLargeError(
                f"The requested {request.bucket} summary would produce about {estimated} buckets; "
                "the maximum is 400. Use a coarser bucket or narrower range."
            )
        prices = await self._market.get_pool_prices(
            PoolPriceRequest(start=start, end=end, include_forecast=False),
            paginate=False,
        )
        load = None
        warnings = list(prices.warnings)
        if request.include_load:
            load = await self._market.get_load(
                LoadRequest(start=start, end=end, include_forecast=False),
                paginate=False,
            )
            warnings.extend(load.warnings)

        price_groups: dict[datetime, list[float]] = defaultdict(list)
        for row in prices.intervals:
            key = chronological_instant(_bucket_start(row.interval_start, request.bucket))
            price_groups[key].append(row.pool_price_cad_per_mwh)
        load_groups: dict[datetime, list[float]] = defaultdict(list)
        if load is not None:
            for row in load.intervals:
                key = chronological_instant(_bucket_start(row.interval_start, request.bucket))
                load_groups[key].append(row.load_mw)

        keys = sorted(set(price_groups) | set(load_groups))
        buckets: list[MarketHistoryBucket] = []
        for key in keys:
            bucket_start = to_market(key)
            price_values = price_groups[key]
            load_values = load_groups[key]
            buckets.append(
                MarketHistoryBucket(
                    interval_start=bucket_start,
                    interval_end=_bucket_end(bucket_start, request.bucket),
                    price_observations=len(price_values),
                    average_pool_price_cad_per_mwh=(mean(price_values) if price_values else None),
                    median_pool_price_cad_per_mwh=(median(price_values) if price_values else None),
                    minimum_pool_price_cad_per_mwh=(min(price_values) if price_values else None),
                    maximum_pool_price_cad_per_mwh=(max(price_values) if price_values else None),
                    hours_at_or_above_100_cad_per_mwh=sum(value >= 100.0 for value in price_values),
                    load_observations=len(load_values),
                    average_load_mw=mean(load_values) if load_values else None,
                    minimum_load_mw=min(load_values) if load_values else None,
                    maximum_load_mw=max(load_values) if load_values else None,
                )
            )
        source_metadata = [prices.metadata] + ([load.metadata] if load is not None else [])
        available = sorted(
            {series for metadata in source_metadata for series in metadata.available_series}
            or ({"pool_price", "load"} if load is not None else {"pool_price"})
        )
        missing = sorted(
            {series for metadata in source_metadata for series in metadata.missing_series}
        )
        degraded = any(
            metadata.completeness
            in {DataCompleteness.PARTIAL, DataCompleteness.DEGRADED, DataCompleteness.EMPTY}
            for metadata in source_metadata
        )
        if not buckets:
            missing = sorted(set(missing) | set(available))
        return MarketHistorySummaryResponse(
            buckets=buckets,
            metadata=DatasetMetadata(
                dataset="Market History Summary",
                source_product="Derived from Pool Price and Alberta Internal Load",
                retrieved_at=utc_now(),
                served_at=utc_now(),
                status=DataStatus.ACTUAL,
                observation_type=ObservationType.DERIVED,
                finality=FinalityStatus.UNKNOWN,
                completeness=(
                    DataCompleteness.EMPTY
                    if not buckets
                    else DataCompleteness.DEGRADED
                    if degraded or missing
                    else DataCompleteness.COMPLETE
                ),
                available_series=available if buckets else [],
                missing_series=missing,
                units={
                    "pool_price_cad_per_mwh": "CAD/MWh",
                    "load_mw": "MW",
                },
                observation_granularity=request.bucket,
                request_start=start,
                request_end=end,
                provider=ProviderName.DERIVED,
                observation_count=len(buckets),
            ),
            warnings=warnings,
        )

    async def assess_supply_tightness(self) -> SupplyTightnessResponse:
        snapshot = await self._market.get_market_snapshot()
        report_date = to_market(snapshot.observed_at).date()
        cached = await self._cache.get_or_set_with_metadata(
            ("generation_capacity", report_date.isoformat(), report_date.isoformat()),
            lambda: self._provider.get_generation_capacity(report_date, report_date),
            ttl_s=self._settings.cache_ttl_snapshot_s,
        )
        capacity, prov = cached.value
        same_hour = _nearest_capacity_hour(capacity, snapshot.observed_at)
        available = _sum_present(row.available_capability_mw for row in same_hour)
        maximum = _sum_present(row.maximum_capability_mw for row in same_hour)
        operating_outage = _sum_present(row.operating_outage_mw for row in same_hour)
        mothball_outage = _sum_present(row.mothball_outage_mw for row in same_hour)
        load = snapshot.alberta_internal_load_mw
        interchange = snapshot.net_interchange_mw
        reserve = snapshot.contingency_reserve_required_mw

        gross_margin = None
        if available is not None and load is not None and interchange is not None:
            gross_margin = available + interchange - load
        adjusted_margin = (
            gross_margin - reserve if gross_margin is not None and reserve is not None else None
        )
        adjusted_pct = adjusted_margin / load if adjusted_margin is not None and load else None
        if adjusted_margin is None:
            signal = "unknown"
        elif adjusted_margin <= 0:
            signal = "tight"
        elif adjusted_pct is not None and adjusted_pct < 0.05:
            signal = "watch"
        else:
            signal = "comfortable"

        warnings = list(snapshot.warnings)
        if not same_hour:
            warnings.append("No generation-capacity interval was available near the snapshot time.")
        return SupplyTightnessResponse(
            observed_at=snapshot.observed_at,
            alberta_internal_load_mw=load,
            available_generation_capability_mw=available,
            maximum_generation_capability_mw=maximum,
            operating_outage_mw=operating_outage,
            mothball_outage_mw=mothball_outage,
            net_interchange_mw=interchange,
            contingency_reserve_required_mw=reserve,
            gross_supply_margin_mw=gross_margin,
            reserve_adjusted_margin_mw=adjusted_margin,
            reserve_adjusted_margin_pct_of_load=adjusted_pct,
            tightness_signal=signal,  # type: ignore[arg-type]
            methodology=(
                "gross margin = available generation capability + net imports - Alberta "
                "Internal Load; reserve-adjusted margin subtracts the contingency reserve "
                "requirement. tight means the adjusted margin is non-positive; watch means it "
                "is positive but below 5% of load. This is a deterministic screening signal, "
                "not an AESO declaration or a causal price explanation."
            ),
            metadata=_metadata(
                "Supply Tightness Indicators",
                prov,
                cached.info,
                count=len(same_hour),
                status=DataStatus.PRELIMINARY,
                observation_type=ObservationType.DERIVED,
                finality=FinalityStatus.PRELIMINARY,
                units={
                    "alberta_internal_load_mw": "MW",
                    "available_generation_capability_mw": "MW",
                    "maximum_generation_capability_mw": "MW",
                    "operating_outage_mw": "MW",
                    "mothball_outage_mw": "MW",
                    "net_interchange_mw": "MW",
                    "contingency_reserve_required_mw": "MW",
                    "gross_supply_margin_mw": "MW",
                    "reserve_adjusted_margin_mw": "MW",
                    "reserve_adjusted_margin_pct_of_load": "ratio",
                },
                granularity="current hourly screening indicator",
                report_start=report_date,
                report_end=report_date,
                completeness=(
                    DataCompleteness.DEGRADED
                    if not same_hour
                    or snapshot.metadata.completeness
                    in {DataCompleteness.PARTIAL, DataCompleteness.DEGRADED}
                    else DataCompleteness.COMPLETE
                ),
                available_series=[
                    series
                    for series, value in (
                        ("load", load),
                        ("generation_capacity", available),
                        ("net_interchange", interchange),
                        ("contingency_reserve_requirement", reserve),
                    )
                    if value is not None
                ],
                missing_series=[
                    series
                    for series, value in (
                        ("load", load),
                        ("generation_capacity", available),
                        ("net_interchange", interchange),
                        ("contingency_reserve_requirement", reserve),
                    )
                    if value is None
                ],
            ),
            warnings=warnings,
        )


def _page[T](records: Sequence[T], offset: int, limit: int) -> tuple[list[T], PageInfo]:
    total = len(records)
    values = list(records[offset : offset + limit])
    next_offset = offset + len(values) if offset + len(values) < total else None
    return values, PageInfo(
        offset=offset,
        limit=limit,
        returned=len(values),
        total=total,
        next_offset=next_offset,
    )


def _page_or_all[T](
    records: Sequence[T], offset: int, limit: int, paginate: bool
) -> tuple[list[T], PageInfo]:
    if paginate:
        return _page(records, offset, limit)
    total = len(records)
    return list(records), PageInfo(
        offset=0,
        limit=max(total, 1),
        returned=total,
        total=total,
        next_offset=None,
    )


def _metadata(
    dataset: str,
    prov: Mapping[str, object],
    cache: CacheInfo,
    *,
    count: int,
    status: DataStatus,
    observation_type: ObservationType,
    finality: FinalityStatus,
    units: dict[str, str],
    granularity: str,
    report_start: date,
    report_end: date,
    completeness: DataCompleteness | None = None,
    available_series: Sequence[str] | None = None,
    missing_series: Sequence[str] | None = None,
) -> DatasetMetadata:
    provider_raw = str(prov.get("provider", ProviderName.AESO_APIM.value))
    provider = (
        ProviderName(provider_raw)
        if provider_raw in {candidate.value for candidate in ProviderName}
        else ProviderName.AESO_APIM
    )
    return DatasetMetadata(
        dataset=dataset,
        source_product=_optional_text(prov.get("source_product")),
        api_version=_optional_text(prov.get("api_version")),
        retrieved_at=cache.retrieved_at,
        served_at=cache.served_at,
        cache_hit=cache.cache_hit,
        cache_age=cache.cache_age,
        status=status,
        observation_type=observation_type,
        finality=finality,
        completeness=(
            completeness
            if completeness is not None
            else DataCompleteness.COMPLETE
            if count
            else DataCompleteness.EMPTY
        ),
        available_series=list(available_series or []),
        missing_series=list(missing_series or []),
        units=units,
        observation_granularity=granularity,
        request_start=start_of_market_day(report_start),
        request_end=end_of_market_day(report_end),
        provider=provider,
        observation_count=count,
    )


def _validate_report_date(
    value: date,
    *,
    earliest: date | None = None,
    latest: date | None = None,
    label: str,
) -> None:
    if earliest is not None and value < earliest:
        raise InvalidDateRangeError(f"Invalid {label}: earliest available date is {earliest}.")
    if latest is not None and value > latest:
        raise InvalidDateRangeError(f"Invalid {label}: latest available date is {latest}.")


def _validate_date_range(
    start: date,
    end: date,
    *,
    earliest: date | None = None,
    latest: date | None = None,
    max_days: int,
    label: str,
    max_future_days: int | None = None,
) -> None:
    if end < start:
        raise InvalidDateRangeError(f"Invalid {label}: end_date must be on or after start_date.")
    if earliest is not None and start < earliest:
        raise InvalidDateRangeError(f"Invalid {label}: earliest available date is {earliest}.")
    if latest is not None and end > latest:
        raise InvalidDateRangeError(f"Invalid {label}: latest available date is {latest}.")
    days = (end - start).days + 1
    if days > max_days:
        raise InvalidDateRangeError(
            f"Invalid {label}: requested {days} calendar days exceeds the maximum of {max_days}."
        )
    if max_future_days is not None:
        latest_future = market_now().date() + timedelta(days=max_future_days)
        if end > latest_future:
            raise InvalidDateRangeError(
                f"Invalid {label}: end_date cannot be later than {latest_future}."
            )


def _date_range_ttl(settings: Settings, end: date) -> float:
    return (
        settings.cache_ttl_historical_s
        if end < market_now().date()
        else settings.cache_ttl_snapshot_s
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _estimated_bucket_count(start: datetime, end: datetime, bucket: str) -> int:
    elapsed_days = max((chronological_instant(end) - chronological_instant(start)).days, 1)
    if bucket == "hour":
        return elapsed_days * 24 + 24
    if bucket == "day":
        return elapsed_days + 2
    if bucket == "week":
        return elapsed_days // 7 + 2
    return elapsed_days // 28 + 2


def _bucket_start(value: datetime, bucket: str) -> datetime:
    local = to_market(value)
    if bucket == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        return start_of_market_day(local.date())
    if bucket == "week":
        return start_of_market_day(local.date() - timedelta(days=local.weekday()))
    return start_of_market_day(local.date().replace(day=1))


def _bucket_end(start: datetime, bucket: str) -> datetime:
    if bucket == "hour":
        return add_elapsed(start, timedelta(hours=1))
    if bucket == "day":
        return end_of_market_day(start.date())
    if bucket == "week":
        return start_of_market_day(start.date() + timedelta(days=7))
    next_month = (
        date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    )
    return start_of_market_day(next_month)


def _nearest_capacity_hour(
    rows: Sequence[GenerationCapacityInterval], observed_at: datetime
) -> list[GenerationCapacityInterval]:
    if not rows:
        return []
    target = chronological_instant(observed_at)
    timestamps = {chronological_instant(row.interval_start) for row in rows}
    past = [timestamp for timestamp in timestamps if timestamp <= target]
    selected = max(past) if past else min(timestamps)
    return [row for row in rows if chronological_instant(row.interval_start) == selected]


def _directive_sort_key(row: Any, fallback_date: date) -> tuple[datetime, str]:
    timestamp = row.operation_start or row.begins_at or row.issued_at
    if timestamp is None:
        timestamp = start_of_market_day(fallback_date)
    return chronological_instant(timestamp), row.asset_id


def _sum_present(values: Any) -> float | None:
    present = [float(value) for value in values if value is not None]
    return sum(present) if present else None
