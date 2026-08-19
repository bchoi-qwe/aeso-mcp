# SPDX-License-Identifier: MIT
"""Deterministic analytics over AESO market observations."""

from __future__ import annotations

from statistics import mean, median

from aeso_mcp.config import Settings
from aeso_mcp.errors import AesoMcpError, AuthenticationError, InvalidDateRangeError
from aeso_mcp.models.analytics import (
    AssociatedChange,
    CompareForecastToActualRequest,
    CompareForecastToActualResponse,
    CompareMarketPeriodsRequest,
    CompareMarketPeriodsResponse,
    ExplainMarketConditionsRequest,
    ExplainMarketConditionsResponse,
    FindPriceEventsRequest,
    FindPriceEventsResponse,
    ForecastActualInterval,
    PeriodStatistics,
    PriceEvent,
)
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DataStatus,
    ObservationType,
    ProviderName,
)
from aeso_mcp.models.generation import LoadRequest
from aeso_mcp.models.prices import PoolPriceRequest
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import (
    chronological_instant,
    elapsed_hours,
    to_market,
    to_utc,
    utc_now,
    validate_range,
)


class AnalyticsService:
    """Server-side calculations so clients need not do large numeric work."""

    def __init__(self, market: MarketService, settings: Settings) -> None:
        self._market = market
        self._settings = settings

    async def compare_market_periods(
        self,
        request: CompareMarketPeriodsRequest,
    ) -> CompareMarketPeriodsResponse:
        a, warnings_a = await self._period_stats(request.period_a_start, request.period_a_end)
        b, warnings_b = await self._period_stats(request.period_b_start, request.period_b_end)
        warnings = warnings_a + warnings_b

        price_delta = None
        price_pct = None
        if a.avg_pool_price_cad_per_mwh is not None and b.avg_pool_price_cad_per_mwh is not None:
            price_delta = b.avg_pool_price_cad_per_mwh - a.avg_pool_price_cad_per_mwh
            if a.avg_pool_price_cad_per_mwh != 0:
                price_pct = price_delta / a.avg_pool_price_cad_per_mwh

        load_delta = None
        load_pct = None
        if a.avg_load_mw is not None and b.avg_load_mw is not None:
            load_delta = b.avg_load_mw - a.avg_load_mw
            if a.avg_load_mw != 0:
                load_pct = load_delta / a.avg_load_mw

        return CompareMarketPeriodsResponse(
            period_a=a,
            period_b=b,
            price_avg_delta_cad_per_mwh=price_delta,
            price_avg_pct_change=price_pct,
            load_avg_delta_mw=load_delta,
            load_avg_pct_change=load_pct,
            metadata=_derived_meta(
                "Market Period Comparison",
                completeness=(DataCompleteness.DEGRADED if warnings else DataCompleteness.COMPLETE),
                available_series=["pool_price"] if warnings else ["pool_price", "load"],
                missing_series=["load"] if warnings else [],
            ),
            warnings=warnings,
        )

    async def find_price_events(
        self,
        request: FindPriceEventsRequest,
    ) -> FindPriceEventsResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_pool_price_days,
            label="price event range",
        )
        prices = await self._market.get_pool_prices(
            PoolPriceRequest(start=start, end=end, include_forecast=False),
            paginate=False,
        )
        values = [i.pool_price_cad_per_mwh for i in prices.intervals]
        warnings: list[str] = []
        missing_series: list[str] = []
        if not values:
            return FindPriceEventsResponse(
                threshold_cad_per_mwh=request.threshold_cad_per_mwh or 0.0,
                events=[],
                metadata=_derived_meta(
                    "Price Event Detection",
                    count=0,
                    completeness=DataCompleteness.EMPTY,
                    available_series=[],
                ),
                warnings=["No pool price observations in the requested range."],
            )

        if request.threshold_cad_per_mwh is not None:
            threshold = request.threshold_cad_per_mwh
        else:
            percentile = request.percentile if request.percentile is not None else 90.0
            threshold = _percentile(values, percentile)

        load_by_start: dict = {}
        try:
            load = await self._market.get_load(
                LoadRequest(start=start, end=end),
                paginate=False,
            )
            load_by_start = {
                chronological_instant(i.interval_start): i.load_mw for i in load.intervals
            }
            if not load.intervals:
                warnings.append(
                    "Load context was unavailable for price events; price-only events are returned."
                )
                missing_series.append("load")
            elif load.metadata.completeness in {
                DataCompleteness.PARTIAL,
                DataCompleteness.DEGRADED,
            }:
                warnings.append(
                    "Load context was partial for price events; event statistics use available load observations."
                )
                missing_series.append("load")
        except AuthenticationError:
            raise
        except AesoMcpError:
            load_by_start = {}
            warnings.append(
                "Load context was unavailable for price events; price-only events are returned."
            )
            missing_series.append("load")

        events: list[PriceEvent] = []
        active: list = []
        ordered = sorted(prices.intervals, key=lambda i: chronological_instant(i.interval_start))
        for interval in ordered:
            if interval.pool_price_cad_per_mwh >= threshold:
                if active and to_utc(active[-1].interval_end) != to_utc(interval.interval_start):
                    event = _close_event(active, load_by_start, request.min_duration_hours)
                    if event:
                        events.append(event)
                    active = []
                active.append(interval)
            elif active:
                event = _close_event(active, load_by_start, request.min_duration_hours)
                if event:
                    events.append(event)
                active = []
        if active:
            event = _close_event(active, load_by_start, request.min_duration_hours)
            if event:
                events.append(event)

        return FindPriceEventsResponse(
            threshold_cad_per_mwh=threshold,
            events=events,
            metadata=_derived_meta(
                "Price Event Detection",
                count=len(events),
                completeness=(
                    DataCompleteness.DEGRADED if missing_series else DataCompleteness.COMPLETE
                ),
                available_series=["pool_price"] if missing_series else ["pool_price", "load"],
                missing_series=missing_series,
            ),
            warnings=warnings,
        )

    async def explain_market_conditions(
        self,
        request: ExplainMarketConditionsRequest,
    ) -> ExplainMarketConditionsResponse:
        focus_start, focus_end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_pool_price_days,
            label="focus window",
        )
        duration = to_utc(focus_end) - to_utc(focus_start)
        if (request.baseline_start is None) ^ (request.baseline_end is None):
            raise InvalidDateRangeError(
                "Provide both baseline_start and baseline_end, or omit both to use the "
                "immediately preceding equal-length window."
            )
        if request.baseline_start is not None and request.baseline_end is not None:
            baseline_start, baseline_end = validate_range(
                request.baseline_start,
                request.baseline_end,
                max_days=self._settings.max_pool_price_days,
                label="baseline window",
            )
        else:
            baseline_end = focus_start
            baseline_start = to_market(to_utc(focus_start) - duration)
            if to_utc(baseline_end) <= to_utc(baseline_start):
                raise InvalidDateRangeError("Unable to infer a valid baseline window.")

        focus, focus_warnings = await self._period_stats(focus_start, focus_end)
        baseline, baseline_warnings = await self._period_stats(baseline_start, baseline_end)

        changes = [
            _change(
                "avg_pool_price",
                focus.avg_pool_price_cad_per_mwh,
                baseline.avg_pool_price_cad_per_mwh,
                "CAD/MWh",
            ),
            _change(
                "max_pool_price",
                focus.max_pool_price_cad_per_mwh,
                baseline.max_pool_price_cad_per_mwh,
                "CAD/MWh",
            ),
            _change(
                "avg_load",
                focus.avg_load_mw,
                baseline.avg_load_mw,
                "MW",
            ),
            _change(
                "max_load",
                focus.max_load_mw,
                baseline.max_load_mw,
                "MW",
            ),
        ]

        notable: list[str] = []
        for change in changes:
            if change.pct_change is None:
                continue
            if abs(change.pct_change) >= 0.2:
                direction = "higher" if change.pct_change > 0 else "lower"
                notable.append(
                    f"{change.metric} was {abs(change.pct_change) * 100:.1f}% {direction} "
                    f"than the baseline window."
                )

        observed = {
            "avg_pool_price_cad_per_mwh": focus.avg_pool_price_cad_per_mwh,
            "max_pool_price_cad_per_mwh": focus.max_pool_price_cad_per_mwh,
            "min_pool_price_cad_per_mwh": focus.min_pool_price_cad_per_mwh,
            "avg_load_mw": focus.avg_load_mw,
            "max_load_mw": focus.max_load_mw,
            "observation_count": float(focus.observation_count),
        }

        return ExplainMarketConditionsResponse(
            focus_start=to_market(focus_start),
            focus_end=to_market(focus_end),
            baseline_start=to_market(baseline_start),
            baseline_end=to_market(baseline_end),
            observed_conditions=observed,
            associated_changes=changes,
            notable_movements=notable,
            metadata=_derived_meta(
                "Market Condition Evidence",
                completeness=(
                    DataCompleteness.DEGRADED
                    if focus_warnings or baseline_warnings
                    else DataCompleteness.COMPLETE
                ),
                available_series=(
                    ["pool_price"]
                    if focus_warnings or baseline_warnings
                    else ["pool_price", "load"]
                ),
                missing_series=["load"] if focus_warnings or baseline_warnings else [],
            ),
            warnings=[
                "Associated changes are correlational evidence only; "
                "they do not establish causation.",
                *focus_warnings,
                *baseline_warnings,
            ],
        )

    async def compare_forecast_to_actual(
        self,
        request: CompareForecastToActualRequest,
    ) -> CompareForecastToActualResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_load_days,
            label="forecast comparison range",
        )
        load = await self._market.get_load(
            LoadRequest(start=start, end=end, include_forecast=True),
            paginate=False,
        )
        missing_series = list(load.metadata.missing_series)
        pairs: list[ForecastActualInterval] = []
        for interval in load.intervals:
            if interval.load_forecast_mw is None:
                continue
            error = interval.load_mw - interval.load_forecast_mw
            abs_error = abs(error)
            abs_pct = abs_error / abs(interval.load_mw) if interval.load_mw != 0 else None
            pairs.append(
                ForecastActualInterval(
                    interval_start=interval.interval_start,
                    interval_end=interval.interval_end,
                    actual_load_mw=interval.load_mw,
                    forecast_load_mw=interval.load_forecast_mw,
                    error_mw=error,
                    abs_error_mw=abs_error,
                    abs_pct_error=abs_pct,
                )
            )

        warnings: list[str] = []
        warnings.extend(load.warnings)
        if missing_series:
            warnings.append(
                "Forecast comparison is partial because the following series were unavailable: "
                + ", ".join(missing_series)
                + "."
            )
        if not pairs:
            warnings.append(
                "No paired forecast/actual load observations were available for this range."
            )
            return CompareForecastToActualResponse(
                observation_count=0,
                intervals=[],
                metadata=_derived_meta(
                    "Load Forecast vs Actual",
                    count=0,
                    completeness=DataCompleteness.EMPTY,
                    available_series=["actual_load"] if load.intervals else [],
                    missing_series=missing_series or ["load_forecast"],
                ),
                warnings=warnings,
            )

        errors = [p.error_mw for p in pairs]
        abs_errors = [p.abs_error_mw for p in pairs]
        abs_pcts = [p.abs_pct_error for p in pairs if p.abs_pct_error is not None]
        rmse = (sum(e * e for e in errors) / len(errors)) ** 0.5

        # Keep response bounded for large windows.
        max_intervals = 168
        truncated = len(pairs) > max_intervals
        if truncated:
            warnings.append(
                f"Returning the first {max_intervals} paired intervals; "
                "summary statistics cover the full matched set."
            )

        return CompareForecastToActualResponse(
            observation_count=len(pairs),
            mean_error_mw=mean(errors),
            mean_abs_error_mw=mean(abs_errors),
            rmse_mw=rmse,
            mean_abs_pct_error=mean(abs_pcts) if abs_pcts else None,
            max_abs_error_mw=max(abs_errors),
            intervals=pairs[:max_intervals],
            metadata=_derived_meta(
                "Load Forecast vs Actual",
                count=len(pairs),
                completeness=(
                    DataCompleteness.DEGRADED if missing_series else DataCompleteness.COMPLETE
                ),
                available_series=["actual_load"] + ([] if missing_series else ["load_forecast"]),
                missing_series=missing_series,
            ),
            warnings=warnings,
        )

    async def _period_stats(self, start, end) -> tuple[PeriodStatistics, list[str]]:
        start_m, end_m = validate_range(
            start,
            end,
            max_days=self._settings.max_pool_price_days,
            label="analytics period",
        )
        prices = await self._market.get_pool_prices(
            PoolPriceRequest(start=start_m, end=end_m, include_forecast=False),
            paginate=False,
        )
        price_vals = [i.pool_price_cad_per_mwh for i in prices.intervals]

        load_vals: list[float] = []
        warnings: list[str] = []
        try:
            load = await self._market.get_load(
                LoadRequest(start=start_m, end=end_m),
                paginate=False,
            )
            load_vals = [i.load_mw for i in load.intervals]
            if not load.intervals:
                warnings.append(
                    f"Load context was unavailable for {start_m.isoformat()} to {end_m.isoformat()}; "
                    "price statistics remain available."
                )
            elif load.metadata.completeness in {
                DataCompleteness.PARTIAL,
                DataCompleteness.DEGRADED,
            }:
                warnings.append(
                    f"Load context was partial for {start_m.isoformat()} to {end_m.isoformat()}; "
                    "statistics use available load observations."
                )
        except AuthenticationError:
            raise
        except AesoMcpError:
            load_vals = []
            warnings.append(
                f"Load context was unavailable for {start_m.isoformat()} to {end_m.isoformat()}; "
                "price statistics remain available."
            )

        return (
            PeriodStatistics(
                start=start_m,
                end=end_m,
                observation_count=len(price_vals),
                avg_pool_price_cad_per_mwh=mean(price_vals) if price_vals else None,
                min_pool_price_cad_per_mwh=min(price_vals) if price_vals else None,
                max_pool_price_cad_per_mwh=max(price_vals) if price_vals else None,
                median_pool_price_cad_per_mwh=median(price_vals) if price_vals else None,
                avg_load_mw=mean(load_vals) if load_vals else None,
                min_load_mw=min(load_vals) if load_vals else None,
                max_load_mw=max(load_vals) if load_vals else None,
            ),
            warnings,
        )


def _close_event(active: list, load_by_start: dict, min_hours: float) -> PriceEvent | None:
    if not active:
        return None
    start = active[0].interval_start
    end = active[-1].interval_end
    duration = elapsed_hours(start, end)
    if duration < min_hours:
        return None
    prices = [i.pool_price_cad_per_mwh for i in active]
    loads = [
        load_by_start[chronological_instant(i.interval_start)]
        for i in active
        if chronological_instant(i.interval_start) in load_by_start
    ]
    return PriceEvent(
        start=start,
        end=end,
        duration_hours=duration,
        peak_price_cad_per_mwh=max(prices),
        average_price_cad_per_mwh=mean(prices),
        avg_load_mw=mean(loads) if loads else None,
        max_load_mw=max(loads) if loads else None,
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if percentile <= 0:
        return ordered[0]
    if percentile >= 100:
        return ordered[-1]
    rank = (len(ordered) - 1) * (percentile / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _change(
    metric: str,
    focus: float | None,
    baseline: float | None,
    unit: str,
) -> AssociatedChange:
    absolute = None
    pct = None
    if focus is not None and baseline is not None:
        absolute = focus - baseline
        if baseline != 0:
            pct = absolute / baseline
    return AssociatedChange(
        metric=metric,
        focus_value=focus,
        baseline_value=baseline,
        absolute_change=absolute,
        pct_change=pct,
        unit=unit,
    )


def _derived_meta(
    dataset: str,
    count: int | None = None,
    *,
    completeness: DataCompleteness = DataCompleteness.UNKNOWN,
    available_series: list[str] | None = None,
    missing_series: list[str] | None = None,
) -> DatasetMetadata:
    served_at = utc_now()
    return DatasetMetadata(
        dataset=dataset,
        source_product="Derived analytics",
        retrieved_at=served_at,
        served_at=served_at,
        status=DataStatus.ACTUAL,
        observation_type=ObservationType.DERIVED,
        completeness=completeness,
        available_series=(
            list(available_series) if available_series is not None else ["pool_price", "load"]
        ),
        missing_series=list(missing_series) if missing_series is not None else [],
        units={"pool_price_cad_per_mwh": "CAD/MWh", "load_mw": "MW"},
        provider=ProviderName.DERIVED,
        observation_count=count,
    )
