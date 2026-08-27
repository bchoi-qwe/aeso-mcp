# SPDX-License-Identifier: MIT
"""Deterministic analytics over AESO market observations."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from itertools import pairwise
from statistics import mean, median
from typing import Any

from aeso_mcp.config import Settings
from aeso_mcp.errors import (
    AesoMcpError,
    AuthenticationError,
    InvalidDateRangeError,
    UnsupportedDatasetError,
)
from aeso_mcp.models.analytics import (
    AssetEnergyRevenueRequest,
    AssetEnergyRevenueResponse,
    AssetEnergyRevenueResult,
    AssociatedChange,
    CompareForecastToActualRequest,
    CompareForecastToActualResponse,
    CompareMarketPeriodsRequest,
    CompareMarketPeriodsResponse,
    CsdMeteredComparisonRequest,
    CsdMeteredComparisonResponse,
    CsdMeteredComparisonResult,
    ExplainMarketConditionsRequest,
    ExplainMarketConditionsResponse,
    FindPriceEventsRequest,
    FindPriceEventsResponse,
    ForecastActualInterval,
    ForecastAnalyticsSeries,
    ForecastErrorAnalyticsByHour,
    ForecastErrorAnalyticsByLeadTime,
    ForecastErrorAnalyticsInterval,
    ForecastErrorAnalyticsRequest,
    ForecastErrorAnalyticsResponse,
    PeriodStatistics,
    PriceEvent,
    RampAnalysisRequest,
    RampAnalysisResponse,
    RampInterval,
    SupplySurplusAnalysisRequest,
    SupplySurplusAnalysisResponse,
    SupplySurplusEventMarketEvidence,
)
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DataStatus,
    ObservationType,
    ProviderName,
)
from aeso_mcp.models.forecasts import OfficialForecastRequest
from aeso_mcp.models.generation import GenerationRequest, LoadRequest
from aeso_mcp.models.history import ForecastRequest, HistoricalGenerationRequest
from aeso_mcp.models.operations import MeteredVolumeRequest
from aeso_mcp.models.prices import PoolPriceRequest
from aeso_mcp.models.reports import SupplySurplusEventsRequest
from aeso_mcp.services.market import MarketService
from aeso_mcp.timeutil import (
    chronological_instant,
    elapsed_hours,
    in_half_open_range,
    to_market,
    to_utc,
    utc_now,
    validate_range,
)


class AnalyticsService:
    """Server-side calculations so clients need not do large numeric work."""

    def __init__(
        self,
        market: MarketService,
        settings: Settings,
        operations: Any | None = None,
        history: Any | None = None,
        forecasts: Any | None = None,
        reports: Any | None = None,
    ) -> None:
        self._market = market
        self._settings = settings
        # Revenue/CSD/asset analytics use the shared operational and history
        # services when supplied by the application dependency graph.
        self._operations = operations
        self._history = history
        self._forecasts = forecasts
        self._reports = reports

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
        prices = await self._get_pool_prices(
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
            load = await self._get_load(
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
        load = await self._get_load(
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

    async def calculate_asset_energy_revenue(
        self,
        request: AssetEnergyRevenueRequest,
    ) -> AssetEnergyRevenueResponse:
        """Join hourly metered MWh to hourly Pool Price without settlement claims."""
        operations = self._require_dependency(self._operations, "metered-volume operations")
        start, end = validate_range(
            request.start,
            request.end,
            max_days=16,
            label="asset energy revenue range",
        )
        prices = await self._get_pool_prices(
            PoolPriceRequest(start=start, end=end, include_forecast=False),
            paginate=False,
        )
        metered = await self._get_metered_volumes(operations, request.asset_ids, start, end)
        price_by_time = {
            chronological_instant(item.interval_start): item.pool_price_cad_per_mwh
            for item in prices.intervals
            if in_half_open_range(item.interval_start, start, end)
        }
        meter_by_asset: dict[str, dict[datetime, float]] = defaultdict(dict)
        duplicate_meter_rows = 0
        expected = _hourly_instants(start, end)
        for item in metered.intervals:
            key = chronological_instant(item.interval_start)
            asset_id = item.asset_id.strip().upper()
            if asset_id not in request.asset_ids or key not in expected:
                continue
            if key in meter_by_asset[asset_id]:
                duplicate_meter_rows += 1
                continue
            meter_by_asset[asset_id][key] = item.metered_volume_mwh

        results: list[AssetEnergyRevenueResult] = []
        for asset_id in request.asset_ids:
            meter = meter_by_asset.get(asset_id, {})
            matched_keys = sorted(set(meter) & set(price_by_time))
            matched_mwh = sum(meter[key] for key in matched_keys)
            gross_revenue = sum(meter[key] * price_by_time[key] for key in matched_keys)
            market_prices = [price_by_time[key] for key in matched_keys]
            realized = gross_revenue / matched_mwh if matched_mwh != 0 else None
            average_market = mean(market_prices) if market_prices else None
            results.append(
                AssetEnergyRevenueResult(
                    asset_id=asset_id,
                    matched_mwh=matched_mwh,
                    gross_energy_revenue_cad=gross_revenue,
                    realized_price_cad_per_mwh=realized,
                    average_market_price_cad_per_mwh=average_market,
                    capture_rate=(
                        realized / average_market
                        if realized is not None and average_market not in (None, 0)
                        else None
                    ),
                    matched_observations=len(matched_keys),
                    missing_intervals=len(expected - set(matched_keys)),
                    missing_metered_intervals=len(expected - set(meter)),
                    missing_price_intervals=len(expected - set(price_by_time)),
                )
            )

        warnings = list(dict.fromkeys([*prices.warnings, *metered.warnings]))
        if duplicate_meter_rows:
            warnings.append(
                f"Ignored {duplicate_meter_rows} duplicate metered rows at the same asset/hour."
            )
        if getattr(metered.page, "total", len(metered.intervals)) > len(metered.intervals):
            warnings.append(
                "Metered-volume results were truncated; missing intervals may reflect the "
                "upstream response limit. Narrow the asset list or date range."
            )
        if any(item.missing_intervals for item in results):
            warnings.append(
                "Revenue uses only matched hourly metered-energy and Pool Price observations; "
                "unmatched intervals remain unobserved and are not treated as zero."
            )
        return AssetEnergyRevenueResponse(
            results=results,
            metadata=_derived_meta(
                "Gross Asset Pool Price Energy Revenue",
                count=sum(item.matched_observations for item in results),
                start=start,
                end=end,
                completeness=_join_completeness(
                    results,
                    has_metered=any(meter_by_asset.values()),
                    has_prices=bool(price_by_time),
                ),
                available_series=(
                    (["metered_volume"] if any(meter_by_asset.values()) else [])
                    + (["pool_price"] if price_by_time else [])
                ),
                missing_series=(["metered_volume"] if not any(meter_by_asset.values()) else [])
                + (["pool_price"] if not price_by_time else []),
                units={
                    "matched_mwh": "MWh",
                    "gross_energy_revenue_cad": "CAD",
                    "realized_price_cad_per_mwh": "CAD/MWh",
                    "average_market_price_cad_per_mwh": "CAD/MWh",
                    "capture_rate": "ratio",
                },
                extra={
                    "source_products": sorted(
                        {
                            value
                            for value in (
                                prices.metadata.source_product,
                                metered.metadata.source_product,
                            )
                            if value
                        }
                    ),
                    "input_providers": sorted(
                        {
                            prices.metadata.provider.value,
                            metered.metadata.provider.value,
                        }
                    ),
                    "revenue_scope": "gross Pool Price energy revenue only; not total settlement revenue",
                },
            ),
            warnings=warnings,
        )

    async def compare_csd_to_metered(
        self,
        request: CsdMeteredComparisonRequest,
    ) -> CsdMeteredComparisonResponse:
        """Compare CSD operational MW estimates with metered settlement MWh."""
        history = self._require_dependency(self._history, "historical CSD generation")
        operations = self._require_dependency(self._operations, "metered-volume operations")
        start, end = validate_range(
            request.start,
            request.end,
            max_days=16,
            label="CSD versus metered comparison range",
        )
        csd = await history.get_historical_generation(
            HistoricalGenerationRequest(
                start=start,
                end=end,
                interval="hourly",
                asset_ids=request.asset_ids,
                limit=2_000,
            ),
            paginate=False,
        )
        metered = await self._get_metered_volumes(operations, request.asset_ids, start, end)
        expected = _hourly_instants(start, end)
        csd_by_asset: dict[str, dict[datetime, float]] = defaultdict(dict)
        duplicate_csd_rows = 0
        for item in csd.intervals:
            asset_id = item.asset_id.strip().upper()
            key = chronological_instant(item.interval_start)
            if asset_id not in request.asset_ids or key not in expected:
                continue
            estimate = item.generation_mw * max(
                elapsed_hours(item.interval_start, item.interval_end), 0.0
            )
            if key in csd_by_asset[asset_id]:
                duplicate_csd_rows += 1
                continue
            csd_by_asset[asset_id][key] = estimate

        metered_by_asset: dict[str, dict[datetime, float]] = defaultdict(dict)
        duplicate_meter_rows = 0
        for item in metered.intervals:
            asset_id = item.asset_id.strip().upper()
            key = chronological_instant(item.interval_start)
            if asset_id not in request.asset_ids or key not in expected:
                continue
            if key in metered_by_asset[asset_id]:
                duplicate_meter_rows += 1
                continue
            metered_by_asset[asset_id][key] = item.metered_volume_mwh

        results: list[CsdMeteredComparisonResult] = []
        for asset_id in request.asset_ids:
            csd_values = csd_by_asset.get(asset_id, {})
            meter_values = metered_by_asset.get(asset_id, {})
            matched_keys = sorted(set(csd_values) & set(meter_values))
            differences = [csd_values[key] - meter_values[key] for key in matched_keys]
            absolute = [abs(value) for value in differences]
            percentage = [
                abs(csd_values[key] - meter_values[key]) / abs(meter_values[key])
                for key in matched_keys
                if meter_values[key] != 0
            ]
            results.append(
                CsdMeteredComparisonResult(
                    asset_id=asset_id,
                    matched_hours=len(matched_keys),
                    operational_generation_estimate_mwh=sum(
                        csd_values[key] for key in matched_keys
                    ),
                    metered_energy_mwh=sum(meter_values[key] for key in matched_keys),
                    mean_signed_difference_mwh=mean(differences) if differences else None,
                    mean_absolute_difference_mwh=mean(absolute) if absolute else None,
                    maximum_absolute_difference_mwh=max(absolute) if absolute else None,
                    mean_absolute_percentage_difference=mean(percentage) if percentage else None,
                    missing_csd_hours=len(expected - set(csd_values)),
                    missing_metered_hours=len(expected - set(meter_values)),
                )
            )

        warnings = list(dict.fromkeys([*csd.warnings, *metered.warnings]))
        if duplicate_csd_rows or duplicate_meter_rows:
            warnings.append(
                "Duplicate asset/hour observations were ignored before comparison "
                f"(CSD={duplicate_csd_rows}, metered={duplicate_meter_rows})."
            )
        if any(item.missing_csd_hours or item.missing_metered_hours for item in results):
            warnings.append(
                "CSD operational average MW was converted to interval MWh and compared with "
                "metered energy only where both source concepts were observed; missing values "
                "were not filled with zero."
            )
        return CsdMeteredComparisonResponse(
            results=results,
            metadata=_derived_meta(
                "Historical CSD versus Metered Energy Comparison",
                count=sum(item.matched_hours for item in results),
                start=start,
                end=end,
                completeness=_comparison_completeness(
                    results,
                    has_csd=any(csd_by_asset.values()),
                    has_metered=any(metered_by_asset.values()),
                ),
                available_series=(
                    (["historical_csd_generation"] if any(csd_by_asset.values()) else [])
                    + (["metered_volume"] if any(metered_by_asset.values()) else [])
                ),
                missing_series=(
                    ["historical_csd_generation"] if not any(csd_by_asset.values()) else []
                )
                + (["metered_volume"] if not any(metered_by_asset.values()) else []),
                units={
                    "operational_generation_estimate_mwh": "MWh",
                    "metered_energy_mwh": "MWh",
                    "mean_signed_difference_mwh": "MWh",
                    "mean_absolute_difference_mwh": "MWh",
                    "mean_absolute_percentage_difference": "ratio",
                },
                extra={
                    "source_products": sorted(
                        {
                            value
                            for value in (
                                csd.metadata.source_product,
                                metered.metadata.source_product,
                            )
                            if value
                        }
                    ),
                    "comparison_semantics": (
                        "CSD operational generation is an average-MW source concept; metered "
                        "energy is an hourly settlement-volume source concept."
                    ),
                },
            ),
            warnings=warnings,
        )

    async def analyze_ramps(self, request: RampAnalysisRequest) -> RampAnalysisResponse:
        """Calculate ramps only across consecutive observations at the requested cadence."""
        start, end = validate_range(
            request.start,
            request.end,
            max_days=7 if request.cadence == "5-minute" else 31,
            label="ramp analysis range",
        )
        points, warnings = await self._ramp_points(request, start, end)
        expected_hours = 1.0 if request.cadence == "hourly" else 5.0 / 60.0
        expected_seconds = expected_hours * 3_600
        ordered = sorted(points, key=lambda row: row[0])
        ramps: list[RampInterval] = []
        skipped_gaps = 0
        for (previous_time, previous_value), (current_time, current_value) in pairwise(ordered):
            elapsed_seconds = (current_time - previous_time).total_seconds()
            if abs(elapsed_seconds - expected_seconds) > 0.001:
                skipped_gaps += 1
                continue
            delta = current_value - previous_value
            direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
            ramps.append(
                RampInterval(
                    interval_start=to_market(previous_time),
                    interval_end=to_market(current_time),
                    from_value=previous_value,
                    to_value=current_value,
                    delta_mw=delta,
                    ramp_rate_mw_per_hour=delta / expected_hours,
                    direction=direction,
                )
            )
        if skipped_gaps:
            warnings.append(
                f"Skipped {skipped_gaps} non-consecutive interval pairs; ramps do not bridge "
                "missing observations."
            )
        up = [item for item in ramps if item.delta_mw > 0]
        down = [item for item in ramps if item.delta_mw < 0]
        percentiles = {
            str(percentile): _percentile([item.delta_mw for item in ramps], percentile)
            for percentile in request.percentiles
        }
        return RampAnalysisResponse(
            series=request.series,
            cadence=request.cadence,
            cadence_minutes=60 if request.cadence == "hourly" else 5,
            observation_count=len(ordered),
            ramp_observation_count=len(ramps),
            maximum_up_ramp_mw=max((item.delta_mw for item in up), default=None),
            maximum_down_ramp_mw=min((item.delta_mw for item in down), default=None),
            maximum_up_ramp_mw_per_hour=max(
                (item.ramp_rate_mw_per_hour for item in up), default=None
            ),
            maximum_down_ramp_mw_per_hour=min(
                (item.ramp_rate_mw_per_hour for item in down), default=None
            ),
            ramp_percentiles_mw=percentiles,
            largest_ramps=sorted(ramps, key=lambda item: abs(item.delta_mw), reverse=True)[
                : request.largest_interval_count
            ],
            metadata=_derived_meta(
                "Cadence-Explicit Ramp Analysis",
                count=len(ramps),
                start=start,
                end=end,
                completeness=(DataCompleteness.COMPLETE if ramps else DataCompleteness.EMPTY),
                available_series=[request.series] if ordered else [],
                missing_series=[request.series] if not ordered else [],
                units={
                    "value": "MW",
                    "delta_mw": "MW per interval",
                    "ramp_rate_mw_per_hour": "MW/hour",
                },
                extra={
                    "requested_cadence": request.cadence,
                    "cadence_minutes": 60 if request.cadence == "hourly" else 5,
                },
            ),
            warnings=list(dict.fromkeys(warnings)),
        )

    async def analyze_forecast_error(
        self,
        request: ForecastErrorAnalyticsRequest,
    ) -> ForecastErrorAnalyticsResponse:
        """Compute source-agnostic forecast errors without inferring missing forecasts."""
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_pool_price_days,
            label="forecast error range",
        )
        pairs, unit, warnings, missing_forecasts, missing_actuals = await self._forecast_pairs(
            request.series, start, end
        )
        errors = [forecast - actual for _, _, actual, forecast, _ in pairs]
        abs_errors = [abs(value) for value in errors]
        pct_errors = [
            abs(forecast - actual) / abs(actual)
            for _, _, actual, forecast, _ in pairs
            if actual != 0
        ]
        intervals = [
            ForecastErrorAnalyticsInterval(
                interval_start=timestamp,
                interval_end=interval_end,
                actual_value=actual,
                forecast_value=forecast,
                error=forecast - actual,
                absolute_error=abs(forecast - actual),
                absolute_percentage_error=(
                    abs(forecast - actual) / abs(actual) if actual != 0 else None
                ),
                unit=unit,
                lead_time_hours=lead_time,
            )
            for timestamp, interval_end, actual, forecast, lead_time in pairs[:1_000]
        ]
        if len(pairs) > len(intervals):
            warnings.append(
                "Summary statistics cover all matched pairs; returned forecast intervals are "
                "bounded to the first 1,000 observations."
            )
        by_hour_values: dict[int, list[float]] = defaultdict(list)
        for timestamp, _end, actual, forecast, _lead in pairs:
            by_hour_values[to_market(timestamp).hour].append(forecast - actual)
        by_hour = [
            ForecastErrorAnalyticsByHour(
                market_hour=hour,
                observation_count=len(values),
                mean_error=mean(values),
                mean_absolute_error=mean(abs(value) for value in values),
                root_mean_squared_error=(mean(value**2 for value in values)) ** 0.5,
            )
            for hour, values in sorted(by_hour_values.items())
        ]
        by_lead_values: dict[float, list[float]] = defaultdict(list)
        for _timestamp, _end, actual, forecast, lead_time in pairs:
            if lead_time is not None:
                by_lead_values[lead_time].append(forecast - actual)
        by_lead_time = [
            ForecastErrorAnalyticsByLeadTime(
                lead_time_hours=lead_time,
                observation_count=len(values),
                mean_error=mean(values),
                mean_absolute_error=mean(abs(value) for value in values),
                root_mean_squared_error=(mean(value**2 for value in values)) ** 0.5,
            )
            for lead_time, values in sorted(by_lead_values.items())
        ]
        if missing_forecasts:
            warnings.append(f"Excluded {missing_forecasts} intervals without forecasts.")
        if missing_actuals:
            warnings.append(f"Excluded {missing_actuals} intervals without actuals.")
        return ForecastErrorAnalyticsResponse(
            series=request.series,
            observation_count=len(errors),
            mean_error=mean(errors) if errors else None,
            mean_absolute_error=mean(abs_errors) if abs_errors else None,
            root_mean_squared_error=(mean(value**2 for value in errors) ** 0.5 if errors else None),
            mean_absolute_percentage_error=mean(pct_errors) if pct_errors else None,
            error_percentiles=(
                {
                    str(percentile): _percentile(abs_errors, percentile)
                    for percentile in request.percentiles
                }
                if abs_errors
                else {}
            ),
            missing_forecast_count=missing_forecasts,
            missing_actual_count=missing_actuals,
            by_market_hour=by_hour,
            by_lead_time=by_lead_time,
            intervals=intervals,
            metadata=_derived_meta(
                "General Forecast Error Analysis",
                count=len(errors),
                start=start,
                end=end,
                completeness=(
                    DataCompleteness.EMPTY
                    if not errors
                    else DataCompleteness.PARTIAL
                    if missing_forecasts or missing_actuals
                    else DataCompleteness.COMPLETE
                ),
                available_series=[f"{request.series}_actual", f"{request.series}_forecast"]
                if errors
                else [],
                missing_series=(
                    ([f"{request.series}_forecast"] if missing_forecasts else [])
                    + ([f"{request.series}_actual"] if missing_actuals else [])
                ),
                units={"error": unit, "absolute_percentage_error": "ratio"},
                extra={"error_sign": "forecast minus actual"},
            ),
            warnings=list(dict.fromkeys(warnings)),
        )

    async def analyze_supply_surplus_events(
        self,
        request: SupplySurplusAnalysisRequest,
    ) -> SupplySurplusAnalysisResponse:
        """Align observed market data to explicit AESO supply-surplus status runs."""
        reports = self._require_dependency(self._reports, "supply-surplus reports")
        start, end = validate_range(
            request.start,
            request.end,
            max_days=8,
            label="supply-surplus analysis range",
        )
        surplus = await reports.get_supply_surplus_events(
            SupplySurplusEventsRequest(start=start, end=end, limit=2_000),
            paginate=False,
        )
        prices = await self._get_pool_prices(
            PoolPriceRequest(start=start, end=end, include_forecast=False, limit=2_000),
            paginate=False,
        )
        warnings = [*surplus.warnings, *prices.warnings]

        loads = []
        try:
            load_response = await self._get_load(
                LoadRequest(start=start, end=end, limit=2_000),
                paginate=False,
            )
            loads = load_response.intervals
            warnings.extend(load_response.warnings)
        except AuthenticationError:
            raise
        except AesoMcpError as exc:
            warnings.append(f"AIL context was unavailable for surplus analysis ({exc.code}).")

        renewable_by_time: dict[datetime, float] = defaultdict(float)
        try:
            generation = await self._market.get_generation(
                GenerationRequest(start=start, end=end, limit=2_000),
                paginate=False,
            )
            for item in generation.intervals:
                if item.fuel_type.upper() in {"WIND", "SOLAR"}:
                    renewable_by_time[chronological_instant(item.interval_start)] += (
                        item.generation_mw
                    )
            warnings.extend(generation.warnings)
        except AuthenticationError:
            raise
        except AesoMcpError as exc:
            warnings.append(
                f"Wind/solar context was unavailable for surplus analysis ({exc.code})."
            )

        evidence: list[SupplySurplusEventMarketEvidence] = []
        for event in surplus.events:
            observed_end = event.end or end
            event_prices = [
                item.pool_price_cad_per_mwh
                for item in prices.intervals
                if in_half_open_range(item.interval_start, event.start, observed_end)
            ]
            event_loads = [
                item.load_mw
                for item in loads
                if in_half_open_range(item.interval_start, event.start, observed_end)
            ]
            event_renewable = [
                value
                for timestamp, value in renewable_by_time.items()
                if in_half_open_range(timestamp, event.start, observed_end)
            ]
            evidence.append(
                SupplySurplusEventMarketEvidence(
                    start=event.start,
                    end=event.end,
                    duration_hours=(
                        elapsed_hours(event.start, event.end) if event.end is not None else None
                    ),
                    status=event.status,
                    average_pool_price_cad_per_mwh=(mean(event_prices) if event_prices else None),
                    minimum_pool_price_cad_per_mwh=(min(event_prices) if event_prices else None),
                    maximum_pool_price_cad_per_mwh=(max(event_prices) if event_prices else None),
                    average_load_mw=mean(event_loads) if event_loads else None,
                    average_renewable_generation_mw=(
                        mean(event_renewable) if event_renewable else None
                    ),
                    price_observation_count=len(event_prices),
                    load_observation_count=len(event_loads),
                    renewable_observation_count=len(event_renewable),
                )
            )

        bounded = [item for item in evidence if item.duration_hours is not None]
        warnings.append(
            "Market values are observed associations during AESO-published surplus states; "
            "they do not establish causation. Historical interchange is not included because "
            "no aligned source is exposed by this analysis."
        )
        return SupplySurplusAnalysisResponse(
            event_count=len(evidence),
            explicitly_bounded_event_count=len(bounded),
            total_explicit_duration_hours=sum(item.duration_hours or 0.0 for item in bounded),
            events=evidence,
            metadata=_derived_meta(
                "Supply Surplus Market Associations",
                count=len(evidence),
                start=start,
                end=end,
                completeness=(
                    DataCompleteness.EMPTY
                    if not evidence
                    else DataCompleteness.DEGRADED
                    if not loads or not renewable_by_time
                    else DataCompleteness.COMPLETE
                ),
                available_series=(
                    ["supply_surplus", "pool_price"]
                    + (["load"] if loads else [])
                    + (["renewable_generation"] if renewable_by_time else [])
                ),
                missing_series=(
                    ([] if loads else ["load"])
                    + ([] if renewable_by_time else ["renewable_generation"])
                    + ["historical_interchange"]
                ),
                units={
                    "duration_hours": "h",
                    "pool_price": "CAD/MWh",
                    "load": "MW",
                    "renewable_generation": "MW",
                },
                extra={"interpretation": "descriptive association only"},
            ),
            warnings=list(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _require_dependency(value: Any | None, name: str) -> Any:
        if value is None:
            raise UnsupportedDatasetError(
                f"This analytics operation requires the configured {name} service."
            )
        return value

    async def _get_pool_prices(self, request: PoolPriceRequest, *, paginate: bool) -> Any:
        """Prefer complete final local history without changing the live refresh provider."""
        if self._history is not None:
            return await self._history.get_historical_pool_prices(request, paginate=paginate)
        return await self._market.get_pool_prices(request, paginate=paginate)

    async def _get_load(self, request: LoadRequest, *, paginate: bool) -> Any:
        """Prefer complete final local AIL without treating partial storage as authoritative."""
        if self._history is not None:
            return await self._history.get_historical_load(request, paginate=paginate)
        return await self._market.get_load(request, paginate=paginate)

    async def _get_metered_volumes(
        self,
        operations: Any,
        asset_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> Any:
        end_inclusive = to_market(to_utc(end) - timedelta(microseconds=1)).date()
        request = MeteredVolumeRequest(
            start_date=to_market(start).date(),
            end_date=end_inclusive,
            asset_ids=asset_ids,
            limit=2_000,
        )
        method = operations.get_metered_volumes
        try:
            return await method(request, paginate=False)
        except TypeError as exc:
            if "paginate" not in str(exc):
                raise
            return await method(request)

    async def _ramp_points(
        self,
        request: RampAnalysisRequest,
        start: datetime,
        end: datetime,
    ) -> tuple[list[tuple[datetime, float]], list[str]]:
        warnings: list[str] = []
        if request.cadence == "5-minute" and request.series in {"ail", "net_load"}:
            raise UnsupportedDatasetError(
                f"Five-minute ramp analytics are not supported for {request.series}; "
                "the available source is hourly."
            )
        if request.series == "ail":
            response = await self._get_load(LoadRequest(start=start, end=end), paginate=False)
            warnings.extend(response.warnings)
            return [
                (chronological_instant(item.interval_start), item.load_mw)
                for item in response.intervals
            ], warnings

        if request.series == "net_load":
            load = await self._get_load(LoadRequest(start=start, end=end), paginate=False)
            generation = await self._market.get_generation(
                GenerationRequest(start=start, end=end), paginate=False
            )
            renewable: dict[datetime, float] = defaultdict(float)
            for item in generation.intervals:
                if item.fuel_type.upper() in {"WIND", "SOLAR"}:
                    renewable[chronological_instant(item.interval_start)] += item.generation_mw
            warnings.extend([*load.warnings, *generation.warnings])
            values = []
            for item in load.intervals:
                key = chronological_instant(item.interval_start)
                if key in renewable:
                    values.append((key, item.load_mw - renewable[key]))
            if len(values) < len(load.intervals):
                warnings.append(
                    "Net-load ramps exclude hours without an observed wind/solar generation value."
                )
            return values, warnings

        if request.series in {"wind", "solar"} and request.cadence == "hourly":
            generation = await self._market.get_generation(
                GenerationRequest(start=start, end=end), paginate=False
            )
            values_by_time: dict[datetime, float] = defaultdict(float)
            for item in generation.intervals:
                if item.fuel_type.upper() == request.series.upper():
                    values_by_time[chronological_instant(item.interval_start)] += item.generation_mw
            warnings.extend(generation.warnings)
            return sorted(values_by_time.items()), warnings

        history = self._require_dependency(self._history, "historical CSD generation")
        response = await history.get_historical_generation(
            HistoricalGenerationRequest(
                start=start,
                end=end,
                interval=request.cadence,
                asset_ids=request.asset_ids,
                fuel_types=(
                    [request.series.upper()] if request.series in {"wind", "solar"} else []
                ),
                limit=2_000,
            ),
            paginate=False,
        )
        values_by_time: dict[datetime, float] = defaultdict(float)
        for item in response.intervals:
            if (
                request.series in {"wind", "solar"}
                and item.fuel_type.upper() != request.series.upper()
            ):
                continue
            values_by_time[chronological_instant(item.interval_start)] += item.generation_mw
        warnings.extend(response.warnings)
        return sorted(values_by_time.items()), warnings

    async def _forecast_pairs(
        self,
        series: ForecastAnalyticsSeries,
        start: datetime,
        end: datetime,
    ) -> tuple[
        list[tuple[datetime, datetime | None, float, float, float | None]], str, list[str], int, int
    ]:
        warnings: list[str] = []
        pairs: list[tuple[datetime, datetime | None, float, float, float | None]] = []
        missing_forecasts = 0
        missing_actuals = 0
        if series == "ail":
            if self._history is not None:
                response = await self._history.get_forecast(
                    ForecastRequest(start=start, end=end, series="ail", limit=2_000),
                    paginate=False,
                )
                warnings.extend(response.warnings)
                for item in response.intervals:
                    if item.forecast_value is None:
                        missing_forecasts += 1
                        continue
                    pairs.append(
                        (
                            item.interval_start,
                            item.interval_end,
                            item.actual_value,
                            item.forecast_value,
                            _lead_time_hours(item),
                        )
                    )
                return pairs, "MW", warnings, missing_forecasts, missing_actuals
            response = await self._get_load(
                LoadRequest(start=start, end=end, include_forecast=True), paginate=False
            )
            warnings.extend(response.warnings)
            for item in response.intervals:
                if item.load_forecast_mw is None:
                    missing_forecasts += 1
                    continue
                pairs.append(
                    (
                        item.interval_start,
                        item.interval_end,
                        item.load_mw,
                        item.load_forecast_mw,
                        None,
                    )
                )
            return pairs, "MW", warnings, missing_forecasts, missing_actuals

        if series == "pool_price" and self._forecasts is None:
            response = await self._get_pool_prices(
                PoolPriceRequest(start=start, end=end, include_forecast=True), paginate=False
            )
            warnings.extend(response.warnings)
            for item in response.intervals:
                forecast = item.forecast_pool_price_cad_per_mwh
                if forecast is None:
                    missing_forecasts += 1
                    continue
                pairs.append(
                    (
                        item.interval_start,
                        item.interval_end,
                        item.pool_price_cad_per_mwh,
                        forecast,
                        None,
                    )
                )
            return pairs, "CAD/MWh", warnings, missing_forecasts, missing_actuals

        forecasts = self._require_dependency(self._forecasts, "official forecast")
        response = await forecasts.get_forecast(
            OfficialForecastRequest(
                start=start,
                end=end,
                series=series,
                horizon="historical",
                include_actual=True,
                limit=2_000,
            ),
            paginate=False,
        )
        warnings.extend(response.warnings)
        for item in response.intervals:
            if item.actual_value is None:
                missing_actuals += 1
                continue
            if item.forecast_value is None:
                missing_forecasts += 1
                continue
            pairs.append(
                (
                    item.interval_start,
                    item.interval_end,
                    item.actual_value,
                    item.forecast_value,
                    _lead_time_hours(item),
                )
            )
        unit = "MW"
        return pairs, unit, warnings, missing_forecasts, missing_actuals

    async def _period_stats(self, start, end) -> tuple[PeriodStatistics, list[str]]:
        start_m, end_m = validate_range(
            start,
            end,
            max_days=self._settings.max_pool_price_days,
            label="analytics period",
        )
        prices = await self._get_pool_prices(
            PoolPriceRequest(start=start_m, end=end_m, include_forecast=False),
            paginate=False,
        )
        price_vals = [i.pool_price_cad_per_mwh for i in prices.intervals]

        load_vals: list[float] = []
        warnings: list[str] = []
        try:
            load = await self._get_load(
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
    start: datetime | None = None,
    end: datetime | None = None,
    completeness: DataCompleteness = DataCompleteness.UNKNOWN,
    available_series: list[str] | None = None,
    missing_series: list[str] | None = None,
    units: dict[str, str] | None = None,
    extra: dict[str, object] | None = None,
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
        units=units or {"pool_price_cad_per_mwh": "CAD/MWh", "load_mw": "MW"},
        request_start=to_market(start) if start is not None else None,
        request_end=to_market(end) if end is not None else None,
        provider=ProviderName.DERIVED,
        observation_count=count,
        extra=extra or {},
    )


def _hourly_instants(start: datetime, end: datetime) -> set[datetime]:
    """Build an hourly UTC grid without assuming 24 hours per local day."""
    current = to_utc(start)
    end_utc = to_utc(end)
    result: set[datetime] = set()
    while current < end_utc:
        result.add(current)
        current += timedelta(hours=1)
    return result


def _join_completeness(
    results: list[AssetEnergyRevenueResult],
    *,
    has_metered: bool,
    has_prices: bool,
) -> DataCompleteness:
    if not results or not has_metered or not has_prices:
        return DataCompleteness.EMPTY
    return (
        DataCompleteness.COMPLETE
        if all(item.missing_intervals == 0 for item in results)
        else DataCompleteness.PARTIAL
    )


def _comparison_completeness(
    results: list[CsdMeteredComparisonResult],
    *,
    has_csd: bool,
    has_metered: bool,
) -> DataCompleteness:
    if not results or not has_csd or not has_metered:
        return DataCompleteness.EMPTY
    return (
        DataCompleteness.COMPLETE
        if all(item.missing_csd_hours == 0 and item.missing_metered_hours == 0 for item in results)
        else DataCompleteness.PARTIAL
    )


def _lead_time_hours(interval: Any) -> float | None:
    """Normalize forecast lead-time fields from either forecast contract."""
    hours = getattr(interval, "lead_time_hours", None)
    if hours is not None:
        return float(hours)
    minutes = getattr(interval, "lead_time_minutes", None)
    return float(minutes) / 60.0 if minutes is not None else None
