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
from aeso_mcp.models.common import DatasetMetadata, DataStatus, ProviderName
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
        a = await self._period_stats(request.period_a_start, request.period_a_end)
        b = await self._period_stats(request.period_b_start, request.period_b_end)

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
            metadata=DatasetMetadata(
                dataset="Market Period Comparison",
                source_product="Derived from Pool Price + AIL",
                retrieved_at=utc_now(),
                status=DataStatus.ACTUAL,
                units={
                    "pool_price_cad_per_mwh": "CAD/MWh",
                    "load_mw": "MW",
                },
                provider=ProviderName.DERIVED,
            ),
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
            PoolPriceRequest(start=start, end=end, include_forecast=False)
        )
        values = [i.pool_price_cad_per_mwh for i in prices.intervals]
        if not values:
            return FindPriceEventsResponse(
                threshold_cad_per_mwh=request.threshold_cad_per_mwh or 0.0,
                events=[],
                metadata=_derived_meta("Price Event Detection", count=0),
                warnings=["No pool price observations in the requested range."],
            )

        if request.threshold_cad_per_mwh is not None:
            threshold = request.threshold_cad_per_mwh
        else:
            percentile = request.percentile if request.percentile is not None else 90.0
            threshold = _percentile(values, percentile)

        load_by_start: dict = {}
        try:
            load = await self._market.get_load(LoadRequest(start=start, end=end))
            load_by_start = {
                chronological_instant(i.interval_start): i.load_mw for i in load.intervals
            }
        except AuthenticationError:
            raise
        except AesoMcpError:
            load_by_start = {}

        events: list[PriceEvent] = []
        active: list = []
        ordered = sorted(prices.intervals, key=lambda i: chronological_instant(i.interval_start))
        for interval in ordered:
            if interval.pool_price_cad_per_mwh >= threshold:
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
            metadata=_derived_meta("Price Event Detection", count=len(events)),
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

        focus = await self._period_stats(focus_start, focus_end)
        baseline = await self._period_stats(baseline_start, baseline_end)

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
            metadata=_derived_meta("Market Condition Evidence"),
            warnings=[
                "Associated changes are correlational evidence only; "
                "they do not establish causation."
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
        load = await self._market.get_load(LoadRequest(start=start, end=end, include_forecast=True))
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
        if not pairs:
            warnings.append(
                "No paired forecast/actual load observations were available for this range."
            )
            return CompareForecastToActualResponse(
                observation_count=0,
                intervals=[],
                metadata=_derived_meta("Load Forecast vs Actual", count=0),
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
            metadata=_derived_meta("Load Forecast vs Actual", count=len(pairs)),
            warnings=warnings,
        )

    async def _period_stats(self, start, end) -> PeriodStatistics:
        start_m, end_m = validate_range(
            start,
            end,
            max_days=self._settings.max_pool_price_days,
            label="analytics period",
        )
        prices = await self._market.get_pool_prices(
            PoolPriceRequest(start=start_m, end=end_m, include_forecast=False)
        )
        price_vals = [i.pool_price_cad_per_mwh for i in prices.intervals]

        load_vals: list[float] = []
        try:
            load = await self._market.get_load(LoadRequest(start=start_m, end=end_m))
            load_vals = [i.load_mw for i in load.intervals]
        except AuthenticationError:
            raise
        except AesoMcpError:
            load_vals = []

        return PeriodStatistics(
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


def _derived_meta(dataset: str, count: int | None = None) -> DatasetMetadata:
    return DatasetMetadata(
        dataset=dataset,
        source_product="Derived analytics",
        retrieved_at=utc_now(),
        status=DataStatus.ACTUAL,
        units={"pool_price_cad_per_mwh": "CAD/MWh", "load_mw": "MW"},
        provider=ProviderName.DERIVED,
        observation_count=count,
    )
