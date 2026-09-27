# SPDX-License-Identifier: MIT
"""Domain service for the typed official AESO forecast publications."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import sqrt
from statistics import mean
from typing import cast

from aeso_mcp.config import Settings
from aeso_mcp.errors import DataValidationError, InvalidDateRangeError, UnsupportedDatasetError
from aeso_mcp.models.common import (
    DataCompleteness,
    DataStatus,
    FinalityStatus,
    ObservationType,
)
from aeso_mcp.models.forecasts import (
    ForecastErrorResponse,
    ForecastErrorSummary,
    ForecastInterval,
    ForecastResponse,
    ForecastSeries,
    ForecastVintage,
    OfficialForecastRequest,
)
from aeso_mcp.providers.base import AesoDataProvider
from aeso_mcp.providers.forecasts import (
    AesoForecastProvider,
    LiteralForecastHorizon,
    LiteralForecastSeries,
    LiteralRenewableSeries,
)
from aeso_mcp.services.cache import AsyncTTLCache, CacheInfo
from aeso_mcp.services.market import _meta, _paginate
from aeso_mcp.services.ttl import historical_ttl_s
from aeso_mcp.storage.history import HistoricalStore
from aeso_mcp.timeutil import (
    add_elapsed,
    chronological_instant,
    in_half_open_range,
    to_market,
    to_utc,
    validate_range,
)


class ForecastService:
    """Bounded forecast retrieval and source-agnostic forecast error metrics.

    ``provider`` owns the named ETS/public-page request shapes.  The optional
    ``ail_provider`` preserves the existing APIM/GridStatus AIL forecast path
    without making the public-report provider pretend that AIL is one of its
    products.
    """

    def __init__(
        self,
        provider: AesoForecastProvider,
        settings: Settings,
        cache: AsyncTTLCache | None = None,
        *,
        ail_provider: AesoDataProvider | None = None,
        store: HistoricalStore | None = None,
    ) -> None:
        self._provider = provider
        self._ail_provider = ail_provider
        self._settings = settings
        self._cache = cache or AsyncTTLCache(max_entries=settings.cache_max_entries)
        self._store = store

    async def get_forecast(
        self,
        request: OfficialForecastRequest,
        *,
        paginate: bool = True,
    ) -> ForecastResponse:
        """Return one supported forecast series in a DST-safe half-open range."""
        start, end = validate_range(
            request.start,
            request.end,
            max_days=_forecast_max_days(request),
            label="forecast range",
        )

        as_of = _normalize_as_of(request.as_of)
        if as_of is not None:
            stored_horizon = _stored_horizon(request, start, end)
            if request.series == "pool_price" and request.horizon not in {None, "historical"}:
                raise InvalidDateRangeError(
                    "Pool Price forecast uses the Forecast and Actual Pool Price report; "
                    "current renewable horizons are not valid."
                )
            if request.series == "wind_solar" and request.horizon == "historical":
                raise UnsupportedDatasetError(
                    "AESO publishes historical wind and solar files separately; "
                    "no combined historical file is exposed by this service."
                )
            stored = await self._get_stored_as_of(
                request.series,
                start=start,
                end=end,
                as_of=as_of,
                horizon=stored_horizon,
            )
            if stored is not None and stored[1] > 0:
                stored_vintages, total_count = stored
                stored_intervals = [_vintage_to_interval(item) for item in stored_vintages]
                if not request.include_actual:
                    stored_intervals = [
                        item.model_copy(update={"actual_value": None}) for item in stored_intervals
                    ]
                finality = _finality(stored_intervals)
                selected_publications = [
                    item.publication_time
                    for item in stored_intervals
                    if item.publication_time is not None
                ]
                publication_time = (
                    max(selected_publications, key=chronological_instant)
                    if selected_publications
                    else None
                )
                return self._response(
                    request,
                    start=start,
                    end=end,
                    intervals=stored_intervals,
                    total_count=total_count,
                    publication_time=publication_time,
                    provenance={
                        "provider": "derived",
                        "source_product": "Forecast vintage store",
                        "storage": "duckdb",
                        "as_of": as_of.isoformat(),
                        "vintage_selection": "latest_eligible_per_target",
                    },
                    finality=finality,
                    cache_info=None,
                    paginate=paginate,
                    completeness=DataCompleteness.UNKNOWN,
                    warnings=[
                        "Point-in-time results include only eligible vintages persisted in the "
                        "local store; target intervals absent from the store are unobserved."
                    ],
                )

        if request.series == "ail":
            intervals, publication_time, provenance, finality, cache_info = await self._get_ail(
                start, end, include_actual=True
            )
        elif request.series == "pool_price":
            if request.horizon not in {None, "historical"}:
                raise InvalidDateRangeError(
                    "Pool Price forecast uses the Forecast and Actual Pool Price report; "
                    "current renewable horizons are not valid."
                )
            cached = await self._cache.get_or_set_with_metadata(
                ("forecast", "pool_price", start.date().isoformat(), end.date().isoformat()),
                lambda: self._provider.get_pool_price_forecast(start.date(), end.date()),
                ttl_s=historical_ttl_s(self._settings, start, end),
            )
            intervals, publication_time, provenance = cached.value
            finality = _finality(intervals)
            cache_info = cached.info
        elif request.series in {"wind", "solar", "wind_solar"}:
            horizon = request.horizon
            if horizon is None:
                horizon = (
                    "current_12_hour"
                    if (to_utc(end) - to_utc(start)) <= timedelta(hours=12)
                    else "current_7_day"
                )
            if horizon == "historical":
                if request.series == "wind_solar":
                    raise UnsupportedDatasetError(
                        "AESO publishes historical wind and solar files separately; "
                        "no combined historical file is exposed by this service."
                    )
                cached = await self._cache.get_or_set_with_metadata(
                    ("forecast", request.series, "historical", start.isoformat(), end.isoformat()),
                    lambda: self._provider.get_historical_forecast(
                        cast(LiteralRenewableSeries, request.series), start, end
                    ),
                    ttl_s=self._settings.cache_ttl_historical_public_report_s,
                )
            elif horizon in {"current_12_hour", "current_7_day"}:
                if request.series not in {"wind", "solar", "wind_solar"}:
                    raise InvalidDateRangeError("Current forecast horizon requires wind or solar.")
                cached = await self._cache.get_or_set_with_metadata(
                    ("forecast", request.series, horizon),
                    lambda: self._provider.get_current_forecast(
                        cast(LiteralForecastSeries, request.series),
                        cast(LiteralForecastHorizon, horizon),
                    ),
                    ttl_s=self._settings.cache_ttl_forecast_s,
                )
            else:
                raise InvalidDateRangeError(f"Unsupported forecast horizon {horizon!r}.")
            intervals, publication_time, provenance = cached.value
            finality = _finality(intervals)
            cache_info = cached.info
        else:
            raise UnsupportedDatasetError(f"Forecast series {request.series!r} is not supported.")

        intervals = _annotate_intervals(
            intervals,
            provenance=provenance,
            publication_time=publication_time,
            retrieved_at=cache_info.retrieved_at,
        )
        await self._persist_vintages(intervals)
        intervals = _filter_intervals(intervals, start, end)
        warnings: list[str] = []
        if as_of is not None:
            intervals = _select_as_of_vintages(intervals, as_of)
            finality = _finality(intervals)
            if not intervals:
                warnings.append(
                    "No forecast vintage with a known publication or issue time was available "
                    f"at as_of={as_of.isoformat()}."
                )
            else:
                warnings.append(
                    "Point-in-time results include only eligible vintages available in the "
                    "retrieved source; unavailable prior publications are unobserved."
                )
        if not request.include_actual:
            intervals = [item.model_copy(update={"actual_value": None}) for item in intervals]
        finality = _finality(intervals)
        intervals.sort(key=lambda item: chronological_instant(item.interval_start))
        total_count = len(intervals)
        if as_of is not None:
            selected_publications = [
                item.publication_time for item in intervals if item.publication_time is not None
            ]
            publication_time = (
                max(selected_publications, key=chronological_instant)
                if selected_publications
                else None
            )
            provenance = {
                **provenance,
                "as_of": as_of.isoformat(),
                "vintage_selection": "latest_eligible_per_target",
            }
        return self._response(
            request,
            start=start,
            end=end,
            intervals=intervals,
            total_count=total_count,
            publication_time=publication_time,
            provenance=provenance,
            finality=finality,
            cache_info=cache_info,
            paginate=paginate,
            warnings=warnings,
            completeness=(DataCompleteness.UNKNOWN if as_of is not None else None),
        )

    def _response(
        self,
        request: OfficialForecastRequest,
        *,
        start: datetime,
        end: datetime,
        intervals: Sequence[ForecastInterval],
        total_count: int,
        publication_time: datetime | None,
        provenance: dict[str, object],
        finality: FinalityStatus,
        cache_info: CacheInfo | None,
        paginate: bool,
        warnings: Sequence[str] = (),
        completeness: DataCompleteness | None = None,
    ) -> ForecastResponse:
        values, page = _paginate(intervals, request.offset, request.limit, paginate)
        response_warnings = list(warnings)
        expected = _expected_forecast_observations(request, start, end)
        inferred_completeness = (
            DataCompleteness.EMPTY
            if total_count == 0
            else DataCompleteness.PARTIAL
            if expected is not None and total_count < expected
            else DataCompleteness.COMPLETE
        )
        if expected is not None and total_count < expected:
            missing = expected - total_count
            warning = (
                f"{missing} forecast target interval(s) were unobserved in the requested range."
            )
            if warning not in response_warnings:
                response_warnings.append(warning)
        return ForecastResponse(
            intervals=values,
            page=page,
            metadata=_meta(
                dataset=f"AESO {request.series} Forecast",
                prov=provenance,
                status=DataStatus.FORECAST,
                observation_type=ObservationType.FORECAST,
                finality=finality,
                units={"forecast_value": _unit_for_series(request.series)},
                granularity=_granularity(intervals),
                start=start,
                end=end,
                publication_time=publication_time,
                count=total_count,
                cache_info=cache_info,
                available_series=[request.series] if total_count else [],
                expected_observations=expected,
                completeness=(completeness if completeness is not None else inferred_completeness),
            ),
            warnings=response_warnings,
        )

    async def _get_stored_as_of(
        self,
        series: ForecastSeries,
        *,
        start: datetime,
        end: datetime,
        as_of: datetime,
        horizon: str | None = None,
    ) -> tuple[list[ForecastVintage], int] | None:
        store = self._store
        if store is None:
            return None
        available = getattr(store, "dependencies_available", None)
        if available is not None:
            enabled = await asyncio.to_thread(available)
            if not enabled:
                return None
        rows, total = await asyncio.to_thread(
            store.query_forecast,
            series,
            start=start,
            end=end,
            as_of=as_of,
            horizon=horizon,
            paginate=False,
        )
        return rows, total

    async def _persist_vintages(self, intervals: Sequence[ForecastInterval]) -> None:
        store = self._store
        if store is None or not intervals:
            return
        available = getattr(store, "dependencies_available", None)
        if available is not None:
            enabled = await asyncio.to_thread(available)
            if not enabled:
                return
        await asyncio.to_thread(store.upsert_forecast_vintages, intervals)

    def summarize_error(
        self,
        intervals: Sequence[ForecastInterval],
        *,
        series: ForecastSeries | None = None,
    ) -> ForecastErrorSummary:
        """Calculate paired forecast-minus-actual errors without imputation."""
        return summarize_forecast_error(intervals, series=series)

    def error_response(
        self,
        intervals: Sequence[ForecastInterval],
        *,
        series: ForecastSeries | None = None,
        provenance: dict[str, object] | None = None,
    ) -> ForecastErrorResponse:
        """Wrap deterministic error statistics with derived-data metadata."""
        summary = self.summarize_error(intervals, series=series)
        source = dict(provenance or {})
        source.setdefault("provider", "derived")
        source.setdefault("source_product", "Forecast error summary")
        return ForecastErrorResponse(
            summary=summary,
            metadata=_meta(
                dataset="Forecast Error Summary",
                prov=source,
                status=DataStatus.ACTUAL,
                observation_type=ObservationType.DERIVED,
                finality=FinalityStatus.FINAL,
                units={"error": summary.unit},
                granularity="paired observations",
                count=summary.observation_count,
                available_series=[summary.series],
                completeness=(
                    DataCompleteness.COMPLETE
                    if summary.observation_count
                    else DataCompleteness.EMPTY
                ),
            ),
        )

    async def _get_ail(
        self,
        start: datetime,
        end: datetime,
        *,
        include_actual: bool,
    ) -> tuple[
        list[ForecastInterval],
        datetime | None,
        dict[str, object],
        FinalityStatus,
        CacheInfo,
    ]:
        ail_provider = self._ail_provider
        if ail_provider is None:
            raise UnsupportedDatasetError(
                "AIL forecast retrieval remains on the existing APIM/GridStatus provider; "
                "construct ForecastService with ail_provider to request series='ail'."
            )
        cached = await self._cache.get_or_set_with_metadata(
            ("forecast", "ail", start.isoformat(), end.isoformat()),
            lambda: ail_provider.get_load(start, end, include_forecast=True),
            ttl_s=historical_ttl_s(self._settings, start, end),
        )
        rows, provenance = cached.value
        intervals: list[ForecastInterval] = []
        for row in rows:
            interval_start = row.get("interval_start")
            if not isinstance(interval_start, datetime):
                raise DataValidationError("AIL forecast provider returned a malformed interval.")
            interval_end = row.get("interval_end")
            if not isinstance(interval_end, datetime):
                interval_end = add_elapsed(interval_start, timedelta(hours=1))
            actual = _as_float(row.get("load_mw"))
            forecast = _as_float(row.get("load_forecast_mw"))
            if forecast is None and actual is None:
                continue
            intervals.append(
                ForecastInterval(
                    interval_start=to_market(interval_start),
                    interval_end=to_market(interval_end),
                    series="ail",
                    horizon="historical",
                    forecast_value=forecast,
                    actual_value=actual if include_actual else None,
                    unit="MW",
                    source_product=str(provenance.get("source_product", "AIL forecast")),
                    forecast_issue_time=_as_datetime(row.get("forecast_issue_time")),
                    publication_time=_as_datetime(row.get("publication_time")),
                    retrieved_at=_as_datetime(row.get("retrieved_at")),
                    source_version=_provenance_text(row.get("source_version")),
                    source_hash=_provenance_text(row.get("source_hash")),
                    observation_type=ObservationType.FORECAST,
                    finality=FinalityStatus.PRELIMINARY,
                )
            )
        return (
            _filter_intervals(intervals, start, end),
            None,
            provenance,
            _finality(intervals),
            cached.info,
        )


def summarize_forecast_error(
    intervals: Sequence[ForecastInterval],
    *,
    series: ForecastSeries | None = None,
) -> ForecastErrorSummary:
    """Return denominator-aware forecast error metrics for one series."""
    selected = series or (intervals[0].series if intervals else "ail")
    paired: list[float] = []
    excluded_without_forecast = 0
    excluded_without_actual = 0
    units = {item.unit for item in intervals}
    for item in intervals:
        if item.series != selected:
            continue
        if item.forecast_value is None:
            excluded_without_forecast += 1
            continue
        if item.actual_value is None:
            excluded_without_actual += 1
            continue
        paired.append(item.forecast_value - item.actual_value)

    percentage_errors: list[float] = []
    for item in intervals:
        if item.series != selected:
            continue
        forecast = item.forecast_value
        actual = item.actual_value
        if forecast is None or actual is None or actual == 0:
            continue
        percentage_errors.append(abs(forecast - actual) / abs(actual) * 100.0)
    errors = paired
    mape = mean(percentage_errors) if percentage_errors else None
    unit = next(iter(units), _unit_for_series(selected))
    if len(units) > 1:
        unit = "mixed"
    return ForecastErrorSummary(
        series=selected,
        observation_count=len(errors),
        excluded_without_forecast=excluded_without_forecast,
        excluded_without_actual=excluded_without_actual,
        mean_error=mean(errors) if errors else None,
        mean_absolute_error=mean(abs(value) for value in errors) if errors else None,
        rmse=sqrt(mean(value * value for value in errors)) if errors else None,
        mape=mape,
        p50_absolute_error=_percentile([abs(value) for value in errors], 0.50),
        p90_absolute_error=_percentile([abs(value) for value in errors], 0.90),
        p95_absolute_error=_percentile([abs(value) for value in errors], 0.95),
        unit=unit,
    )


def _forecast_max_days(request: OfficialForecastRequest) -> float:
    if request.series == "pool_price":
        return 31
    if request.horizon == "historical":
        return 366 * 3
    if request.series in {"wind", "solar", "wind_solar"}:
        return 8
    return 366


def _stored_horizon(
    request: OfficialForecastRequest,
    start: datetime,
    end: datetime,
) -> str | None:
    """Return the source horizon that a point-in-time store read may select."""

    if request.series == "pool_price":
        return request.horizon or "historical"
    if request.series not in {"wind", "solar", "wind_solar"}:
        # AIL's existing provider contract does not expose a horizon; leave
        # the filter open for backward-compatible stored AIL vintages.
        return None
    if request.horizon is not None:
        return request.horizon
    return (
        "current_12_hour"
        if (to_utc(end) - to_utc(start)) <= timedelta(hours=12)
        else "current_7_day"
    )


def _expected_forecast_observations(
    request: OfficialForecastRequest,
    start: datetime,
    end: datetime,
) -> int | None:
    """Estimate target intervals from the selected source cadence."""

    if request.series not in {"ail", "pool_price", "wind", "solar", "wind_solar"}:
        return None
    if request.series in {"wind", "solar", "wind_solar"}:
        horizon = request.horizon or _stored_horizon(request, start, end)
        cadence = timedelta(minutes=10 if horizon == "current_12_hour" else 60)
    else:
        cadence = timedelta(hours=1)
    return max(0, int((to_utc(end) - to_utc(start)) / cadence))


def _normalize_as_of(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise InvalidDateRangeError("as_of must be timezone-aware.")
    return to_utc(value)


def _annotate_intervals(
    intervals: Sequence[ForecastInterval],
    *,
    provenance: dict[str, object],
    publication_time: datetime | None,
    retrieved_at: datetime,
) -> list[ForecastInterval]:
    """Attach immutable-source metadata needed for vintage persistence."""

    source_version = _provenance_text(
        provenance.get("source_version"), provenance.get("api_version")
    )
    source_hash = _provenance_text(provenance.get("source_hash"))
    annotated: list[ForecastInterval] = []
    for interval in intervals:
        issue_time = interval.forecast_issue_time
        lead_time = interval.lead_time_minutes
        if lead_time is None and issue_time is not None:
            lead_seconds = (to_utc(interval.interval_start) - to_utc(issue_time)).total_seconds()
            if lead_seconds >= 0:
                lead_time = int(lead_seconds / 60)
        annotated.append(
            interval.model_copy(
                update={
                    "publication_time": interval.publication_time or publication_time,
                    "retrieved_at": interval.retrieved_at or retrieved_at,
                    "source_version": interval.source_version or source_version,
                    # A provider without a stable source-object hash still
                    # needs an identity that does not depend on the caller's
                    # range.  Per-target payload hashes deduplicate the same
                    # publication when one fetch is later split into chunks.
                    "source_hash": interval.source_hash or source_hash or _interval_hash(interval),
                    "lead_time_minutes": lead_time,
                }
            )
        )
    return annotated


def _select_as_of_vintages(
    intervals: Sequence[ForecastInterval],
    as_of: datetime,
) -> list[ForecastInterval]:
    """Filter and deterministically select one eligible vintage per target."""

    selected: dict[datetime, ForecastInterval] = {}
    for interval in intervals:
        publication = interval.publication_time
        issue = interval.forecast_issue_time
        known = [value for value in (publication, issue) if value is not None]
        if not known or interval.retrieved_at is None:
            continue
        if to_utc(interval.retrieved_at) > as_of:
            continue
        if any(to_utc(value) > as_of for value in known):
            continue
        target = to_utc(interval.interval_start)
        current = selected.get(target)
        if current is None or _vintage_sort_key(interval) > _vintage_sort_key(current):
            selected[target] = interval
    return sorted(selected.values(), key=lambda item: chronological_instant(item.interval_start))


def _vintage_sort_key(interval: ForecastInterval) -> tuple[object, ...]:
    known = [
        to_utc(value)
        for value in (interval.publication_time, interval.forecast_issue_time)
        if value is not None
    ]
    information_time = max(known, default=datetime.min.replace(tzinfo=UTC))
    retrieved = (
        to_utc(interval.retrieved_at)
        if interval.retrieved_at is not None
        else datetime.min.replace(tzinfo=UTC)
    )
    return (
        information_time,
        to_utc(interval.publication_time)
        if interval.publication_time is not None
        else datetime.min.replace(tzinfo=UTC),
        to_utc(interval.forecast_issue_time)
        if interval.forecast_issue_time is not None
        else datetime.min.replace(tzinfo=UTC),
        retrieved,
        interval.source_version or "",
        interval.source_hash or "",
        interval.vintage_id or "",
    )


def _intervals_hash(intervals: Sequence[ForecastInterval]) -> str:
    payload: list[dict[str, object]] = []
    for interval in intervals:
        value = interval.model_dump(mode="json")
        # The fallback identifies the published forecast payload.  Actuals,
        # finality, and completeness may arrive later as enrichment and must
        # not create a second vintage for the same forecast publication.
        for key in (
            "retrieved_at",
            "vintage_id",
            "source_hash",
            "actual_value",
            "finality",
            "completeness",
        ):
            value.pop(key, None)
        payload.append(value)
    payload.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _interval_hash(interval: ForecastInterval) -> str:
    """Identify one forecast payload without retrieval or later actual state."""

    return _intervals_hash([interval])


def _provenance_text(*values: object) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _vintage_to_interval(vintage: ForecastVintage) -> ForecastInterval:
    return ForecastInterval(
        interval_start=vintage.interval_start,
        interval_end=vintage.interval_end,
        series=vintage.series,
        horizon=vintage.horizon,
        forecast_value=vintage.forecast_value,
        actual_value=vintage.actual_value,
        minimum_value=vintage.minimum_value,
        maximum_value=vintage.maximum_value,
        capacity_mw=vintage.capacity_mw,
        forecast_issue_time=vintage.forecast_issue_time,
        publication_time=vintage.publication_time,
        retrieved_at=vintage.retrieved_at,
        source_version=vintage.source_version,
        source_hash=vintage.source_hash,
        source_file_id=vintage.source_file_id,
        source_file_name=vintage.source_file_name,
        vintage_id=vintage.vintage_id,
        lead_time_minutes=vintage.lead_time_minutes,
        unit=vintage.unit,
        source_product=vintage.source_product,
        observation_type=vintage.observation_type,
        finality=vintage.finality,
        completeness=vintage.completeness,
    )


def _filter_intervals(
    intervals: Sequence[ForecastInterval], start: datetime, end: datetime
) -> list[ForecastInterval]:
    return [item for item in intervals if in_half_open_range(item.interval_start, start, end)]


def _finality(intervals: Sequence[ForecastInterval]) -> FinalityStatus:
    values = {item.finality for item in intervals}
    if len(values) == 1:
        return next(iter(values))
    return FinalityStatus.UNKNOWN


def _granularity(intervals: Sequence[ForecastInterval]) -> str:
    if not intervals:
        return "source-defined"
    widths = {
        round(
            (to_utc(item.interval_end).timestamp() - to_utc(item.interval_start).timestamp()) / 60
        )
        for item in intervals[:5]
    }
    if widths == {10}:
        return "10m"
    if widths == {60}:
        return "1h"
    return "source-defined"


def _unit_for_series(series: str) -> str:
    return "CAD/MWh" if series == "pool_price" else "MW"


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, str | int | float):
        raise DataValidationError(f"Forecast provider returned malformed numeric value {value!r}.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(
            f"Forecast provider returned malformed numeric value {value!r}."
        ) from exc


def _as_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise DataValidationError(f"Forecast provider returned malformed timestamp {value!r}.")
    return value


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


__all__ = ["ForecastService", "summarize_forecast_error"]
