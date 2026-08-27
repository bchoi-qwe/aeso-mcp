# SPDX-License-Identifier: MIT
"""Domain service for the typed official AESO forecast publications."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
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
from aeso_mcp.timeutil import chronological_instant, in_half_open_range, to_market, validate_range


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
    ) -> None:
        self._provider = provider
        self._ail_provider = ail_provider
        self._settings = settings
        self._cache = cache or AsyncTTLCache(max_entries=settings.cache_max_entries)

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

        if request.series == "ail":
            intervals, publication_time, provenance, finality, cache_info = await self._get_ail(
                start, end, include_actual=request.include_actual
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
            intervals = _filter_intervals(intervals, start, end)
            if not request.include_actual:
                intervals = [item.model_copy(update={"actual_value": None}) for item in intervals]
            finality = _finality(intervals)
            cache_info = cached.info
        elif request.series in {"wind", "solar", "wind_solar"}:
            horizon = request.horizon
            if horizon is None:
                horizon = (
                    "current_12_hour" if (end - start) <= timedelta(hours=12) else "current_7_day"
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
            intervals = _filter_intervals(intervals, start, end)
            if not request.include_actual:
                intervals = [item.model_copy(update={"actual_value": None}) for item in intervals]
            finality = _finality(intervals)
            cache_info = cached.info
        else:
            raise UnsupportedDatasetError(f"Forecast series {request.series!r} is not supported.")

        intervals.sort(key=lambda item: chronological_instant(item.interval_start))
        total_count = len(intervals)
        values, page = _paginate(intervals, request.offset, request.limit, paginate)
        series_name = request.series
        return ForecastResponse(
            intervals=values,
            page=page,
            metadata=_meta(
                dataset=f"AESO {series_name} Forecast",
                prov=provenance,
                status=DataStatus.FORECAST,
                observation_type=ObservationType.FORECAST,
                finality=finality,
                units={"forecast_value": _unit_for_series(series_name)},
                granularity=_granularity(intervals),
                start=start,
                end=end,
                publication_time=publication_time,
                count=total_count,
                cache_info=cache_info,
                available_series=[series_name] if total_count else [],
                completeness=(DataCompleteness.COMPLETE if total_count else DataCompleteness.EMPTY),
            ),
        )

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
                interval_end = interval_start + timedelta(hours=1)
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
            (
                item.interval_end.astimezone().timestamp()
                - item.interval_start.astimezone().timestamp()
            )
            / 60
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
