# SPDX-License-Identifier: MIT
"""Domain services for named AESO public operational reports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from aeso_mcp.config import Settings
from aeso_mcp.errors import InvalidDateRangeError
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DataStatus,
    FinalityStatus,
    ObservationType,
)
from aeso_mcp.models.reports import (
    DdsMarketReportRequest,
    DdsMarketReportResponse,
    FfrNetScheduleRequest,
    FfrNetScheduleResponse,
    SupplyAdequacyRequest,
    SupplyAdequacyResponse,
    SupplySurplusEvent,
    SupplySurplusEventsRequest,
    SupplySurplusEventsResponse,
    SupplySurplusInterval,
    SupplySurplusRequest,
    SupplySurplusResponse,
    SystemEventsRequest,
    SystemEventsResponse,
    TmrReferencePriceRequest,
    TmrReferencePriceResponse,
)
from aeso_mcp.providers.reports import AesoReportsProvider
from aeso_mcp.services.cache import AsyncTTLCache, CacheInfo
from aeso_mcp.services.market import _meta, _paginate
from aeso_mcp.timeutil import (
    chronological_instant,
    in_half_open_range,
    start_of_market_day,
    validate_range,
)


class ReportsService:
    """Fetch, bound, cache, and paginate the official named report products."""

    def __init__(
        self,
        provider: AesoReportsProvider,
        settings: Settings,
        cache: AsyncTTLCache | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._cache = cache or AsyncTTLCache(max_entries=settings.cache_max_entries)

    async def get_supply_adequacy(
        self,
        request: SupplyAdequacyRequest,
        *,
        paginate: bool = True,
    ) -> SupplyAdequacyResponse:
        start, end = _optional_range(
            request.start,
            request.end,
            max_days=8,
            label="supply adequacy range",
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("report", "supply_adequacy"),
            self._provider.get_supply_adequacy,
            ttl_s=self._settings.cache_ttl_public_report_s,
        )
        rows, publication_time, provenance = cached.value
        values = _filter_time_rows(rows, start, end)
        page_values, page = _paginate(values, request.offset, request.limit, paginate)
        return SupplyAdequacyResponse(
            intervals=page_values,
            page=page,
            metadata=_report_metadata(
                dataset="AESO Supply Adequacy and Market Supply Cushion",
                provenance=provenance,
                cache_info=cached.info,
                count=len(values),
                status=DataStatus.FORECAST,
                observation_type=ObservationType.FORECAST,
                finality=FinalityStatus.PRELIMINARY,
                granularity="1h",
                units={"status_code": "code", "supply_cushion_code": "code"},
                start=start,
                end=end,
                publication_time=publication_time,
                available_series=["supply_adequacy", "supply_cushion"] if values else [],
            ),
        )

    async def get_supply_surplus(
        self,
        request: SupplySurplusRequest,
        *,
        paginate: bool = True,
    ) -> SupplySurplusResponse:
        start, end = _optional_range(
            request.start,
            request.end,
            max_days=8,
            label="supply surplus range",
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("report", "supply_surplus"),
            self._provider.get_supply_surplus,
            ttl_s=self._settings.cache_ttl_public_report_s,
        )
        rows, publication_time, provenance = cached.value
        values = _filter_time_rows(rows, start, end)
        page_values, page = _paginate(values, request.offset, request.limit, paginate)
        return SupplySurplusResponse(
            intervals=page_values,
            page=page,
            metadata=_report_metadata(
                dataset="AESO Supply Surplus",
                provenance=provenance,
                cache_info=cached.info,
                count=len(values),
                status=DataStatus.FORECAST,
                observation_type=ObservationType.FORECAST,
                finality=FinalityStatus.PRELIMINARY,
                granularity="1h",
                units={"status_code": "code"},
                start=start,
                end=end,
                publication_time=publication_time,
                available_series=["supply_surplus"] if values else [],
            ),
        )

    async def get_supply_surplus_events(
        self,
        request: SupplySurplusEventsRequest,
        *,
        paginate: bool = True,
    ) -> SupplySurplusEventsResponse:
        """Group only explicitly published zero-price states into events.

        A run ending at the final returned report interval remains open.  An
        end time is populated only when a subsequent report interval publishes
        the non-surplus state (or a contiguous run is explicitly broken).
        """
        response = await self.get_supply_surplus(request, paginate=False)
        events = _supply_surplus_events(response.intervals)
        values, page = _paginate(events, request.offset, request.limit, paginate)
        metadata = response.metadata.model_copy(
            update={
                "dataset": "AESO Supply Surplus Events",
                "observation_count": len(events),
                "available_series": ["supply_surplus_events"] if events else [],
                "units": {},
                "observation_granularity": "event",
                "finality": FinalityStatus.PRELIMINARY,
                "status": DataStatus.FORECAST,
                "observation_type": ObservationType.DERIVED,
                "completeness": (DataCompleteness.COMPLETE if events else DataCompleteness.EMPTY),
            }
        )
        return SupplySurplusEventsResponse(events=values, page=page, metadata=metadata)

    async def get_ffr_net_schedule(
        self,
        request: FfrNetScheduleRequest,
        *,
        paginate: bool = True,
    ) -> FfrNetScheduleResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=366,
            label="FFR Net Schedule range",
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("report", "ffr_net_schedule"),
            self._provider.get_ffr_net_schedule,
            ttl_s=self._settings.cache_ttl_historical_public_report_s,
        )
        rows, publication_time, provenance = cached.value
        values = _filter_time_rows(rows, start, end)
        page_values, page = _paginate(values, request.offset, request.limit, paginate)
        return FfrNetScheduleResponse(
            intervals=page_values,
            page=page,
            metadata=_report_metadata(
                dataset="AESO FFR Net Schedule",
                provenance=provenance,
                cache_info=cached.info,
                count=len(values),
                status=DataStatus.FINAL,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.FINAL,
                granularity="1h",
                units={"net_schedule_mw": "MW", "intertie_mw": "MW"},
                start=start,
                end=end,
                publication_time=publication_time,
                available_series=["ffr_net_schedule"] if values else [],
            ),
        )

    async def get_dds_market_report(
        self,
        request: DdsMarketReportRequest,
        *,
        paginate: bool = True,
    ) -> DdsMarketReportResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=31,
            label="DDS report range",
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("report", "dds", start.date().isoformat(), end.date().isoformat()),
            lambda: self._provider.get_dds_market_report(start.date(), end.date()),
            ttl_s=self._settings.cache_ttl_public_report_s,
        )
        rows, publication_time, provenance = cached.value
        values = [row for row in rows if in_half_open_range(row.observed_at, start, end)]
        page_values, page = _paginate(values, request.offset, request.limit, paginate)
        return DdsMarketReportResponse(
            records=page_values,
            page=page,
            metadata=_report_metadata(
                dataset="AESO Dispatch Down Service Market Report",
                provenance=provenance,
                cache_info=cached.info,
                count=len(values),
                status=DataStatus.ACTUAL,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.PRELIMINARY,
                granularity="publication",
                units={"available_dds_mw": "MW"},
                start=start,
                end=end,
                publication_time=publication_time,
                available_series=["dds_available"] if values else [],
            ),
        )

    async def get_tmr_reference_price(
        self,
        request: TmrReferencePriceRequest,
        *,
        paginate: bool = True,
    ) -> TmrReferencePriceResponse:
        start_date, end_date = request.start_date, request.end_date
        start = start_of_market_day(start_date) if start_date else None
        end = start_of_market_day(end_date + timedelta(days=1)) if end_date else None
        cached = await self._cache.get_or_set_with_metadata(
            ("report", "tmr_reference_price"),
            self._provider.get_tmr_reference_price,
            ttl_s=self._settings.cache_ttl_historical_public_report_s,
        )
        rows, publication_time, provenance = cached.value
        values = [
            row
            for row in rows
            if (start_date is None or row.effective_date >= start_date)
            and (end_date is None or row.effective_date <= end_date)
        ]
        page_values, page = _paginate(values, request.offset, request.limit, paginate)
        return TmrReferencePriceResponse(
            records=page_values,
            page=page,
            metadata=_report_metadata(
                dataset="AESO TMR Reference Price",
                provenance=provenance,
                cache_info=cached.info,
                count=len(values),
                status=DataStatus.FINAL,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.FINAL,
                granularity="monthly publication",
                units={"reference_price_cad_per_mwh": "CAD/MWh"},
                start=start,
                end=end,
                publication_time=publication_time,
                available_series=["tmr_reference_price"] if values else [],
            ),
        )

    async def get_system_events(
        self,
        request: SystemEventsRequest,
        *,
        paginate: bool = True,
    ) -> SystemEventsResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=31,
            label="system event range",
        )
        cached = await self._cache.get_or_set_with_metadata(
            ("report", "system_events", start.date().isoformat(), end.date().isoformat()),
            lambda: self._provider.get_system_events(start.date(), end.date()),
            ttl_s=self._settings.cache_ttl_historical_public_report_s,
        )
        rows, publication_time, provenance = cached.value
        values = [row for row in rows if in_half_open_range(row.event_time, start, end)]
        page_values, page = _paginate(values, request.offset, request.limit, paginate)
        return SystemEventsResponse(
            records=page_values,
            page=page,
            metadata=_report_metadata(
                dataset="AESO AIES System Events",
                provenance=provenance,
                cache_info=cached.info,
                count=len(values),
                status=DataStatus.ACTUAL,
                observation_type=ObservationType.ACTUAL,
                finality=FinalityStatus.UNKNOWN,
                granularity="event",
                units={},
                start=start,
                end=end,
                publication_time=publication_time,
                available_series=["system_events"] if values else [],
            ),
        )


def _optional_range(
    start: datetime | None,
    end: datetime | None,
    *,
    max_days: float,
    label: str,
) -> tuple[datetime | None, datetime | None]:
    if (start is None) != (end is None):
        raise InvalidDateRangeError(f"Provide both start and end for {label}.")
    if start is None or end is None:
        return None, None
    return validate_range(start, end, max_days=max_days, label=label)


def _filter_time_rows[T](
    rows: Sequence[T], start: datetime | None, end: datetime | None
) -> list[T]:
    if start is None or end is None:
        return list(rows)
    filtered: list[T] = []
    for row in rows:
        interval_start = getattr(row, "interval_start", None)
        if isinstance(interval_start, datetime) and in_half_open_range(interval_start, start, end):
            filtered.append(row)
    return filtered


def _supply_surplus_events(rows: Sequence[SupplySurplusInterval]) -> list[SupplySurplusEvent]:
    ordered = sorted(rows, key=lambda row: chronological_instant(row.interval_start))
    events: list[SupplySurplusEvent] = []
    current: SupplySurplusInterval | None = None
    current_end: datetime | None = None
    for row in ordered:
        is_surplus = row.status_code in {0, 1}
        if not is_surplus:
            if current is not None:
                events.append(_event(current, current_end or row.interval_start))
                current = None
                current_end = None
            continue
        if current is None:
            current = row
            current_end = row.interval_end
            continue
        contiguous = chronological_instant(row.interval_start) == chronological_instant(
            current_end or current.interval_end
        )
        same_state = row.status_code == current.status_code
        if not contiguous or not same_state:
            events.append(_event(current, current_end if contiguous else None))
            current = row
        current_end = row.interval_end
    if current is not None:
        events.append(_event(current, None))
    return events


def _event(row: SupplySurplusInterval, end: datetime | None) -> SupplySurplusEvent:
    return SupplySurplusEvent(
        start=row.interval_start,
        end=end,
        status=row.status,
        status_code=row.status_code,
        source_confidence="published_status",
    )


def _report_metadata(
    *,
    dataset: str,
    provenance: dict[str, object],
    cache_info: CacheInfo,
    count: int,
    status: DataStatus,
    observation_type: ObservationType,
    finality: FinalityStatus,
    granularity: str,
    units: dict[str, str],
    start: datetime | None,
    end: datetime | None,
    publication_time: datetime | None,
    available_series: Sequence[str],
) -> DatasetMetadata:
    return _meta(
        dataset=dataset,
        prov=provenance,
        status=status,
        observation_type=observation_type,
        finality=finality,
        units=units,
        granularity=granularity,
        start=start,
        end=end,
        publication_time=publication_time,
        count=count,
        cache_info=cache_info,
        available_series=available_series,
        completeness=(DataCompleteness.COMPLETE if count else DataCompleteness.EMPTY),
    )


__all__ = ["ReportsService"]
