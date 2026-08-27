# SPDX-License-Identifier: MIT
"""Deterministic, bounded Alberta electricity-market research analytics."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Awaitable, Sequence
from datetime import date, datetime, timedelta
from itertools import pairwise
from statistics import mean, median, pstdev
from typing import Any, TypeVar

from aeso_mcp.config import Settings
from aeso_mcp.errors import AesoMcpError, AuthenticationError
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DataStatus,
    FinalityStatus,
    ObservationType,
    ProviderName,
)
from aeso_mcp.models.forecasts import OfficialForecastRequest
from aeso_mcp.models.generation import LoadRequest
from aeso_mcp.models.grid import OutagesRequest
from aeso_mcp.models.history import ForecastRequest, HistoricalGenerationRequest
from aeso_mcp.models.operations import (
    DailyPageRequest,
    DateRangePageRequest,
    IntertieCapabilityRequest,
)
from aeso_mcp.models.prices import PoolPriceRequest
from aeso_mcp.models.reports import (
    DdsMarketReportRequest,
    FfrNetScheduleRequest,
    SupplyAdequacyRequest,
    SupplySurplusRequest,
    SystemEventsRequest,
    TmrReferencePriceRequest,
)
from aeso_mcp.models.research import (
    AssetDispatchRequest,
    AssetDispatchResponse,
    AssetDispatchResult,
    CapturePriceRequest,
    CapturePriceResponse,
    CapturePriceResult,
    ForecastErrorByHour,
    ForecastErrorRequest,
    ForecastErrorResponse,
    GenerationAnalysisRequest,
    GenerationMixResponse,
    GenerationMixResult,
    IntertieUtilizationRequest,
    IntertieUtilizationResponse,
    IntertieUtilizationResult,
    MarketEventDemandEvidence,
    MarketEventEvidence,
    MarketEventIntertieEvidence,
    MarketEventMeritOrderEvidence,
    MarketEventOfficialEvidence,
    MarketEventPriceEvidence,
    MarketEventRequest,
    MarketEventReserveEvidence,
    MarketEventResponse,
    MarketEventSupplyEvidence,
    NetLoadInterval,
    NetLoadRequest,
    NetLoadResponse,
    OutageImpactRequest,
    OutageImpactResponse,
    PercentileValue,
    PriceDurationCurveRequest,
    PriceDurationCurveResponse,
    PriceDurationPoint,
    PriceStatisticsRequest,
    PriceStatisticsResponse,
    ResearchMetric,
    SupplyStackRequest,
    SupplyStackResponse,
)
from aeso_mcp.models.reserves import OperatingReserveSummaryRequest
from aeso_mcp.services.grid import GridService
from aeso_mcp.services.history import HistoryService
from aeso_mcp.services.market import MarketService
from aeso_mcp.services.operations import OperationsService
from aeso_mcp.services.reserves import OperatingReserveService
from aeso_mcp.timeutil import chronological_instant, to_market, to_utc, utc_now, validate_range

T = TypeVar("T")


class ResearchService:
    """Compute reproducible summaries over complete provider results."""

    def __init__(
        self,
        market: MarketService,
        history: HistoryService,
        operations: OperationsService,
        grid: GridService,
        reserves: OperatingReserveService,
        settings: Settings,
        forecasts: Any | None = None,
        reports: Any | None = None,
    ) -> None:
        self._market = market
        self._history = history
        self._operations = operations
        self._grid = grid
        self._reserves = reserves
        self._settings = settings
        self._forecasts = forecasts
        self._reports = reports

    async def get_price_statistics(
        self, request: PriceStatisticsRequest
    ) -> PriceStatisticsResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_pool_price_days,
            label="price statistics range",
        )
        response = await self._history.get_historical_pool_prices(
            PoolPriceRequest(start=start, end=end), paginate=False
        )
        values = [item.pool_price_cad_per_mwh for item in response.intervals]
        return _price_statistics_response(
            values,
            request.percentiles,
            start,
            end,
            warnings=response.warnings,
        )

    async def get_price_duration_curve(
        self, request: PriceDurationCurveRequest
    ) -> PriceDurationCurveResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_pool_price_days,
            label="price duration curve range",
        )
        response = await self._history.get_historical_pool_prices(
            PoolPriceRequest(start=start, end=end), paginate=False
        )
        values = sorted((item.pool_price_cad_per_mwh for item in response.intervals), reverse=True)
        points: list[PriceDurationPoint] = []
        if values:
            sample_count = min(request.points, len(values))
            indexes = sorted(
                {
                    round(index * (len(values) - 1) / (sample_count - 1))
                    for index in range(sample_count)
                }
            )
            points = [
                PriceDurationPoint(
                    exceedance_percent=(index + 1) / len(values) * 100,
                    pool_price_cad_per_mwh=values[index],
                )
                for index in indexes
            ]
        return PriceDurationCurveResponse(
            observation_count=len(values),
            points=points,
            metadata=_derived_meta(
                "Pool Price Duration Curve",
                start,
                end,
                len(points),
                units={
                    "exceedance_percent": "percent",
                    "pool_price_cad_per_mwh": "CAD/MWh",
                },
                completeness=_completeness(values),
            ),
            warnings=response.warnings,
        )

    async def analyze_market_event(self, request: MarketEventRequest) -> MarketEventResponse:
        focus_start, focus_end = validate_range(
            request.start,
            request.end,
            max_days=7,
            label="market event focus range",
        )
        duration = to_utc(focus_end) - to_utc(focus_start)
        if request.baseline_start is not None and request.baseline_end is not None:
            baseline_start, baseline_end = validate_range(
                request.baseline_start,
                request.baseline_end,
                max_days=7,
                label="market event baseline range",
            )
        else:
            baseline_end = focus_start
            baseline_start = to_market(to_utc(focus_start) - duration)

        focus_values, focus_evidence, focus_warnings = await self._event_values(
            focus_start, focus_end
        )
        baseline_values, _, baseline_warnings = await self._event_values(
            baseline_start, baseline_end
        )
        units = {
            "average_pool_price": "CAD/MWh",
            "maximum_pool_price": "CAD/MWh",
            "price_volatility": "CAD/MWh",
            "high_price_hours": "hours",
            "average_load": "MW",
            "maximum_load": "MW",
            "maximum_up_ramp": "MW per hourly interval",
            "load_forecast_mae": "MW",
            "average_available_generation_capability": "MW",
            "average_outage_capacity": "MW",
            "renewable_generation_share": "ratio",
            "average_net_load": "MW",
            "marginal_dispatched_offer": "CAD/MWh",
            "offered_energy_volume": "MW",
            "offer_volume_hhi": "ratio",
            "intertie_offer_to_capability_ratio": "ratio",
            "import_available_capability": "MW",
            "export_available_capability": "MW",
            "import_gross_offer": "MW",
            "export_gross_offer": "MW",
            "intertie_capability_outages": "events",
            "unit_commitments": "directives",
            "active_reserve_price": "CAD/MW",
            "standby_reserve_clearing_price": "CAD/MW",
            "active_reserve_volume": "MW",
            "standby_reserve_volume": "MW",
            "standby_reserve_activated_volume": "MW",
            "reserve_offer_control_blocks": "blocks",
            "pool_price_forecast_mae": "CAD/MWh",
            "wind_forecast_mae": "MW",
            "solar_forecast_mae": "MW",
            "average_ffr_net_schedule": "MW",
            "average_dds_available": "MW",
            "tmr_reference_price": "CAD/MWh",
        }
        metrics = [
            _metric(name, focus_values.get(name), baseline_values.get(name), unit)
            for name, unit in units.items()
        ]
        ranked = sorted(
            (
                metric
                for metric in metrics
                if metric.percent_change is not None and metric.name != "average_pool_price"
            ),
            key=lambda item: abs(item.percent_change or 0),
            reverse=True,
        )
        associations = [
            f"{item.name}: {(item.percent_change or 0.0) * 100:+.1f}% versus baseline"
            for item in ranked
        ]
        return MarketEventResponse(
            focus_start=focus_start,
            focus_end=focus_end,
            baseline_start=baseline_start,
            baseline_end=baseline_end,
            evidence=focus_evidence,
            metrics=metrics,
            ranked_associations=associations,
            methodology=(
                "Compares observed focus-window aggregates with a separate baseline window. "
                "Ranked changes are descriptive associations; they do not establish that any "
                "metric caused the observed price outcome."
            ),
            metadata=_derived_meta(
                "Market Event Analysis",
                focus_start,
                focus_end,
                len(metrics),
                units=units,
                completeness=(
                    DataCompleteness.DEGRADED
                    if focus_warnings or baseline_warnings
                    else DataCompleteness.COMPLETE
                ),
                extra={
                    "baseline_start": baseline_start.isoformat(),
                    "baseline_end": baseline_end.isoformat(),
                },
            ),
            warnings=list(dict.fromkeys(focus_warnings + baseline_warnings)),
        )

    async def _event_values(
        self, start: datetime, end: datetime
    ) -> tuple[dict[str, float | None], MarketEventEvidence, list[str]]:
        prices = await self._history.get_historical_pool_prices(
            PoolPriceRequest(start=start, end=end), paginate=False
        )
        loads = await self._history.get_historical_load(
            LoadRequest(start=start, end=end, include_forecast=True), paginate=False
        )
        outages = await self._grid.get_outages(OutagesRequest(start=start, end=end))
        generation = await self._history.get_historical_generation(
            HistoricalGenerationRequest(
                start=start,
                end=end,
                interval="hourly",
            ),
            paginate=False,
        )
        start_date, end_date = _inclusive_market_dates(start, end)
        warnings = prices.warnings + loads.warnings + outages.warnings + generation.warnings
        capacity = await self._optional_event_source(
            "generation capacity",
            self._operations.get_generation_capacity(
                DateRangePageRequest(start_date=start_date, end_date=end_date),
                paginate=False,
            ),
            warnings,
        )
        interties = await self._optional_event_source(
            "intertie capability",
            self._operations.get_intertie_capability(
                IntertieCapabilityRequest(
                    start_date=start_date,
                    end_date=end_date,
                    include_versions=False,
                ),
                paginate=False,
            ),
            warnings,
        )
        intertie_outages = await self._optional_event_source(
            "intertie outages",
            self._operations.get_intertie_outages(
                DateRangePageRequest(start_date=start_date, end_date=end_date),
                paginate=False,
            ),
            warnings,
        )
        commitments = await self._optional_event_source(
            "unit commitments",
            self._operations.get_unit_commitments(
                DateRangePageRequest(start_date=start_date, end_date=end_date),
                paginate=False,
            ),
            warnings,
        )
        reserve_summary = await self._optional_event_source(
            "operating reserves",
            self._reserves.summarize(
                OperatingReserveSummaryRequest(
                    start_date=start_date,
                    end_date=end_date,
                    include_activations=True,
                )
            ),
            warnings,
        )
        merit_blocks = []
        reserve_offer_blocks = []
        for report_date in _dates(start_date, end_date):
            response = await self._optional_event_source(
                f"energy merit order for {report_date.isoformat()}",
                self._operations.get_energy_merit_order(
                    DailyPageRequest(report_date=report_date, limit=2_000),
                    paginate=False,
                ),
                warnings,
            )
            if response is not None:
                merit_blocks.extend(response.blocks)
                warnings.extend(response.warnings)
            reserve_offers = await self._optional_event_source(
                f"operating-reserve offer control for {report_date.isoformat()}",
                self._operations.get_operating_reserve_offer_control(
                    DailyPageRequest(report_date=report_date, limit=2_000),
                    paginate=False,
                ),
                warnings,
            )
            if reserve_offers is not None:
                reserve_offer_blocks.extend(reserve_offers.blocks)
                warnings.extend(reserve_offers.warnings)

        price_values = [item.pool_price_cad_per_mwh for item in prices.intervals]
        load_values = [item.load_mw for item in loads.intervals]
        load_values_by_time = {
            chronological_instant(item.interval_start): item.load_mw for item in loads.intervals
        }
        load_ramps = [right - left for left, right in pairwise(load_values)]
        load_forecast_errors = [
            item.load_forecast_mw - item.load_mw
            for item in loads.intervals
            if item.load_forecast_mw is not None
        ]
        outage_values = [item.total_outage_mw for item in outages.outages]
        generation_by_fuel: dict[str, float] = defaultdict(float)
        renewable_by_time: dict[datetime, float] = defaultdict(float)
        for item in generation.intervals:
            fuel = item.fuel_type.upper()
            generation_by_fuel[fuel] += item.generation_mw
            if fuel in {"WIND", "SOLAR", "HYDRO"}:
                renewable_by_time[item.interval_start_utc] += item.generation_mw
        total_generation = sum(generation_by_fuel.values())
        net_load_values = [
            value - renewable_by_time.get(timestamp, 0.0)
            for timestamp, value in load_values_by_time.items()
        ]

        available_capability_values: list[float] = []
        if capacity is not None:
            available_by_time: dict[datetime, float] = defaultdict(float)
            for item in capacity.intervals:
                if item.available_capability_mw is not None:
                    available_by_time[chronological_instant(item.interval_start)] += (
                        item.available_capability_mw
                    )
            available_capability_values = list(available_by_time.values())
            warnings.extend(capacity.warnings)

        offer_sizes = [
            item.block_size_mw
            if item.block_size_mw is not None
            else max((item.to_mw or 0.0) - (item.from_mw or 0.0), 0.0)
            for item in merit_blocks
        ]
        offered_volume = sum(offer_sizes)
        dispatched_offer_prices = [
            item.block_price_cad_per_mwh
            for item in merit_blocks
            if (item.dispatched_mw or 0.0) > 0 and item.block_price_cad_per_mwh is not None
        ]
        offer_volume_by_asset: dict[str, float] = defaultdict(float)
        for index, (block, volume) in enumerate(zip(merit_blocks, offer_sizes, strict=True)):
            offer_volume_by_asset[block.asset_id or f"unidentified-block-{index}"] += volume
        offer_hhi = (
            sum((volume / offered_volume) ** 2 for volume in offer_volume_by_asset.values())
            if offered_volume
            else None
        )

        capability_values: list[float] = []
        gross_offer_values: list[float] = []
        offer_capability_ratios: list[float] = []
        import_capability_values: list[float] = []
        export_capability_values: list[float] = []
        import_offer_values: list[float] = []
        export_offer_values: list[float] = []
        if interties is not None:
            capability_values = [
                item.available_transfer_capability_mw
                for item in interties.intervals
                if item.available_transfer_capability_mw is not None
            ]
            gross_offer_values = [
                item.gross_offer_mw
                for item in interties.intervals
                if item.gross_offer_mw is not None
            ]
            offer_capability_ratios = [
                item.gross_offer_mw / item.available_transfer_capability_mw
                for item in interties.intervals
                if item.gross_offer_mw is not None
                and item.available_transfer_capability_mw is not None
                and item.available_transfer_capability_mw > 0
            ]
            import_capability_values = [
                item.available_transfer_capability_mw
                for item in interties.intervals
                if item.direction == "import" and item.available_transfer_capability_mw is not None
            ]
            export_capability_values = [
                item.available_transfer_capability_mw
                for item in interties.intervals
                if item.direction == "export" and item.available_transfer_capability_mw is not None
            ]
            import_offer_values = [
                item.gross_offer_mw
                for item in interties.intervals
                if item.direction == "import" and item.gross_offer_mw is not None
            ]
            export_offer_values = [
                item.gross_offer_mw
                for item in interties.intervals
                if item.direction == "export" and item.gross_offer_mw is not None
            ]
            warnings.extend(interties.warnings)

        active_prices: list[float] = []
        standby_prices: list[float] = []
        active_volumes: list[float] = []
        standby_volumes: list[float] = []
        standby_activated_volume = 0.0
        if reserve_summary is not None:
            active_prices = [
                item.average_price_cad_per_mw
                for item in reserve_summary.results
                if item.procurement == "active" and item.average_price_cad_per_mw is not None
            ]
            standby_prices = [
                item.average_price_cad_per_mw
                for item in reserve_summary.results
                if item.procurement == "standby" and item.average_price_cad_per_mw is not None
            ]
            active_volumes = [
                item.average_volume_mw
                for item in reserve_summary.results
                if item.procurement == "active" and item.average_volume_mw is not None
            ]
            standby_volumes = [
                item.average_volume_mw
                for item in reserve_summary.results
                if item.procurement == "standby" and item.average_volume_mw is not None
            ]
            standby_activated_volume = sum(
                item.activated_volume_mw
                for item in reserve_summary.results
                if item.procurement == "standby"
            )
            warnings.extend(reserve_summary.warnings)

        price_evidence = MarketEventPriceEvidence(
            average_cad_per_mwh=_mean(price_values),
            maximum_cad_per_mwh=max(price_values) if price_values else None,
            volatility_cad_per_mwh=pstdev(price_values) if price_values else None,
            high_price_hours=sum(value >= 100 for value in price_values),
        )
        demand_evidence = MarketEventDemandEvidence(
            average_load_mw=_mean(load_values),
            maximum_load_mw=max(load_values) if load_values else None,
            maximum_up_ramp_mw=max(load_ramps) if load_ramps else None,
            forecast_mae_mw=(
                mean(abs(value) for value in load_forecast_errors) if load_forecast_errors else None
            ),
        )
        supply_evidence = MarketEventSupplyEvidence(
            average_available_capability_mw=_mean(available_capability_values),
            average_outage_mw=_mean(outage_values),
            generation_mwh_by_fuel=dict(sorted(generation_by_fuel.items())),
            renewable_generation_share=(
                sum(
                    volume
                    for fuel, volume in generation_by_fuel.items()
                    if fuel in {"WIND", "SOLAR", "HYDRO"}
                )
                / total_generation
                if total_generation
                else None
            ),
            average_net_load_mw=_mean(net_load_values),
        )
        merit_evidence = MarketEventMeritOrderEvidence(
            offer_block_count=len(merit_blocks),
            marginal_dispatched_offer_cad_per_mwh=(
                max(dispatched_offer_prices) if dispatched_offer_prices else None
            ),
            offered_volume_mw=offered_volume if merit_blocks else None,
            offer_volume_hhi=offer_hhi,
        )
        intertie_evidence = MarketEventIntertieEvidence(
            average_available_capability_mw=_mean(capability_values),
            average_gross_offer_mw=_mean(gross_offer_values),
            average_offer_to_capability_ratio=_mean(offer_capability_ratios),
            average_import_available_capability_mw=_mean(import_capability_values),
            average_export_available_capability_mw=_mean(export_capability_values),
            average_import_gross_offer_mw=_mean(import_offer_values),
            average_export_gross_offer_mw=_mean(export_offer_values),
            capability_outage_count=(
                len(intertie_outages.outages) if intertie_outages is not None else 0
            ),
        )
        reserve_evidence = MarketEventReserveEvidence(
            average_active_price_cad_per_mw=_mean(active_prices),
            average_standby_clearing_price_cad_per_mw=_mean(standby_prices),
            average_active_volume_mw=_mean(active_volumes),
            average_standby_volume_mw=_mean(standby_volumes),
            standby_activated_volume_mw=standby_activated_volume,
            offer_control_block_count=len(reserve_offer_blocks),
        )
        official_evidence = await self._official_event_evidence(start, end, warnings)
        evidence = MarketEventEvidence(
            price=price_evidence,
            demand=demand_evidence,
            supply=supply_evidence,
            merit_order=merit_evidence,
            interties=intertie_evidence,
            reserves=reserve_evidence,
            official_reports=official_evidence,
            commitment_count=(len(commitments.directives) if commitments is not None else None),
        )
        return (
            {
                "average_pool_price": price_evidence.average_cad_per_mwh,
                "maximum_pool_price": price_evidence.maximum_cad_per_mwh,
                "price_volatility": price_evidence.volatility_cad_per_mwh,
                "high_price_hours": float(price_evidence.high_price_hours),
                "average_load": demand_evidence.average_load_mw,
                "maximum_load": demand_evidence.maximum_load_mw,
                "maximum_up_ramp": demand_evidence.maximum_up_ramp_mw,
                "load_forecast_mae": demand_evidence.forecast_mae_mw,
                "average_available_generation_capability": (
                    supply_evidence.average_available_capability_mw
                ),
                "average_outage_capacity": supply_evidence.average_outage_mw,
                "renewable_generation_share": supply_evidence.renewable_generation_share,
                "average_net_load": supply_evidence.average_net_load_mw,
                "marginal_dispatched_offer": (merit_evidence.marginal_dispatched_offer_cad_per_mwh),
                "offered_energy_volume": merit_evidence.offered_volume_mw,
                "offer_volume_hhi": merit_evidence.offer_volume_hhi,
                "intertie_offer_to_capability_ratio": (
                    intertie_evidence.average_offer_to_capability_ratio
                ),
                "import_available_capability": (
                    intertie_evidence.average_import_available_capability_mw
                ),
                "export_available_capability": (
                    intertie_evidence.average_export_available_capability_mw
                ),
                "import_gross_offer": intertie_evidence.average_import_gross_offer_mw,
                "export_gross_offer": intertie_evidence.average_export_gross_offer_mw,
                "intertie_capability_outages": float(intertie_evidence.capability_outage_count),
                "unit_commitments": (
                    float(evidence.commitment_count)
                    if evidence.commitment_count is not None
                    else None
                ),
                "active_reserve_price": reserve_evidence.average_active_price_cad_per_mw,
                "standby_reserve_clearing_price": (
                    reserve_evidence.average_standby_clearing_price_cad_per_mw
                ),
                "active_reserve_volume": reserve_evidence.average_active_volume_mw,
                "standby_reserve_volume": reserve_evidence.average_standby_volume_mw,
                "standby_reserve_activated_volume": (reserve_evidence.standby_activated_volume_mw),
                "reserve_offer_control_blocks": float(reserve_evidence.offer_control_block_count),
                "pool_price_forecast_mae": official_evidence.forecast_mae_by_series.get(
                    "pool_price"
                ),
                "wind_forecast_mae": official_evidence.forecast_mae_by_series.get("wind"),
                "solar_forecast_mae": official_evidence.forecast_mae_by_series.get("solar"),
                "average_ffr_net_schedule": official_evidence.average_ffr_net_schedule_mw,
                "average_dds_available": official_evidence.average_dds_available_mw,
                "tmr_reference_price": official_evidence.tmr_reference_price_cad_per_mwh,
            },
            evidence,
            list(dict.fromkeys(warnings)),
        )

    async def _official_event_evidence(
        self,
        start: datetime,
        end: datetime,
        warnings: list[str],
    ) -> MarketEventOfficialEvidence:
        forecast_mae: dict[str, float] = {}
        if self._forecasts is not None:
            for series in ("pool_price", "wind", "solar"):
                response = await self._optional_event_source(
                    f"{series} forecast error context",
                    self._forecasts.get_forecast(
                        OfficialForecastRequest(
                            start=start,
                            end=end,
                            series=series,
                            horizon="historical",
                            include_actual=True,
                            limit=2_000,
                        ),
                        paginate=False,
                    ),
                    warnings,
                )
                if response is None:
                    continue
                paired_errors = [
                    abs(item.forecast_value - item.actual_value)
                    for item in response.intervals
                    if item.forecast_value is not None and item.actual_value is not None
                ]
                if paired_errors:
                    forecast_mae[series] = mean(paired_errors)
                warnings.extend(response.warnings)

        if self._reports is None:
            return MarketEventOfficialEvidence(forecast_mae_by_series=forecast_mae)

        adequacy = await self._optional_event_source(
            "supply adequacy context",
            self._reports.get_supply_adequacy(
                SupplyAdequacyRequest(start=start, end=end, limit=2_000),
                paginate=False,
            ),
            warnings,
        )
        surplus = await self._optional_event_source(
            "supply surplus context",
            self._reports.get_supply_surplus(
                SupplySurplusRequest(start=start, end=end, limit=2_000),
                paginate=False,
            ),
            warnings,
        )
        ffr = await self._optional_event_source(
            "FFR Net Schedule context",
            self._reports.get_ffr_net_schedule(
                FfrNetScheduleRequest(start=start, end=end, limit=2_000),
                paginate=False,
            ),
            warnings,
        )
        dds = await self._optional_event_source(
            "DDS context",
            self._reports.get_dds_market_report(
                DdsMarketReportRequest(start=start, end=end, limit=2_000),
                paginate=False,
            ),
            warnings,
        )
        start_date, end_date = _inclusive_market_dates(start, end)
        tmr = await self._optional_event_source(
            "TMR reference-price context",
            self._reports.get_tmr_reference_price(
                TmrReferencePriceRequest(start_date=start_date, end_date=end_date),
                paginate=False,
            ),
            warnings,
        )
        system_events = await self._optional_event_source(
            "system-event context",
            self._reports.get_system_events(
                SystemEventsRequest(start=start, end=end, limit=2_000),
                paginate=False,
            ),
            warnings,
        )

        for response in (adequacy, surplus, ffr, dds, tmr, system_events):
            if response is not None:
                warnings.extend(response.warnings)

        tmr_prices = (
            [item.reference_price_cad_per_mwh for item in tmr.records] if tmr is not None else []
        )
        return MarketEventOfficialEvidence(
            forecast_mae_by_series=forecast_mae,
            supply_adequacy_statuses=sorted(
                {
                    item.adequacy_status
                    for item in (adequacy.intervals if adequacy is not None else [])
                    if item.adequacy_status is not None
                }
            ),
            supply_cushion_statuses=sorted(
                {
                    item.supply_cushion_status
                    for item in (adequacy.intervals if adequacy is not None else [])
                    if item.supply_cushion_status is not None
                }
            ),
            supply_surplus_statuses=sorted(
                {item.status for item in (surplus.intervals if surplus is not None else [])}
            ),
            average_ffr_net_schedule_mw=(
                _mean([item.net_schedule_mw for item in ffr.intervals]) if ffr is not None else None
            ),
            average_dds_available_mw=(
                _mean([item.available_dds_mw for item in dds.records]) if dds is not None else None
            ),
            tmr_reference_price_cad_per_mwh=(tmr_prices[-1] if tmr_prices else None),
            system_event_comments=(
                [item.comments for item in system_events.records[:25]]
                if system_events is not None
                else []
            ),
        )

    async def _optional_event_source(
        self,
        label: str,
        operation: Awaitable[T],
        warnings: list[str],
    ) -> T | None:
        try:
            return await operation
        except AuthenticationError:
            raise
        except AesoMcpError as exc:
            warnings.append(f"{label} unavailable: {exc.to_client_message()}")
            return None

    async def calculate_capture_prices(self, request: CapturePriceRequest) -> CapturePriceResponse:
        start, end = validate_range(
            request.start, request.end, max_days=31, label="capture price range"
        )
        prices = await self._history.get_historical_pool_prices(
            PoolPriceRequest(start=start, end=end), paginate=False
        )
        generation = await self._history.get_historical_generation(
            HistoricalGenerationRequest(
                start=start,
                end=end,
                interval="hourly",
                asset_ids=request.asset_ids,
                fuel_types=request.fuel_types,
            ),
            paginate=False,
        )
        price_by_time = {
            chronological_instant(item.interval_start): item.pool_price_cad_per_mwh
            for item in prices.intervals
        }
        groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
        asset_filter = {value.upper() for value in request.asset_ids}
        fuel_filter = {value.upper() for value in request.fuel_types}
        for item in generation.intervals:
            price = price_by_time.get(item.interval_start_utc)
            if price is None:
                continue
            if item.asset_id.upper() in asset_filter:
                groups[f"asset:{item.asset_id}"].append((item.generation_mw, price))
            if item.fuel_type.upper() in fuel_filter:
                groups[f"fuel:{item.fuel_type}"].append((item.generation_mw, price))
        market_average = _mean(list(price_by_time.values()))
        results: list[CapturePriceResult] = []
        for group, observations in sorted(groups.items()):
            generation_mwh = sum(max(volume, 0.0) for volume, _ in observations)
            capture = (
                sum(max(volume, 0.0) * price for volume, price in observations) / generation_mwh
                if generation_mwh
                else None
            )
            results.append(
                CapturePriceResult(
                    group=group,
                    generation_mwh=generation_mwh,
                    capture_price_cad_per_mwh=capture,
                    market_average_price_cad_per_mwh=market_average,
                    capture_rate=(
                        capture / market_average
                        if capture is not None
                        and market_average is not None
                        and market_average != 0
                        else None
                    ),
                    matched_intervals=len(observations),
                )
            )
        warnings = prices.warnings + generation.warnings
        if not results:
            warnings.append("No generation observations matched hourly pool prices.")
        return CapturePriceResponse(
            results=results,
            metadata=_derived_meta(
                "Capture Price Analysis",
                start,
                end,
                len(results),
                units={
                    "generation_mwh": "MWh",
                    "capture_price_cad_per_mwh": "CAD/MWh",
                    "capture_rate": "ratio",
                },
                completeness=_completeness(results),
            ),
            warnings=list(dict.fromkeys(warnings)),
        )

    async def analyze_net_load(self, request: NetLoadRequest) -> NetLoadResponse:
        start, end = validate_range(request.start, request.end, max_days=31, label="net load range")
        loads = await self._history.get_historical_load(
            LoadRequest(start=start, end=end), paginate=False
        )
        generation = await self._history.get_historical_generation(
            HistoricalGenerationRequest(
                start=start,
                end=end,
                interval="hourly",
                fuel_types=request.renewable_fuels,
            ),
            paginate=False,
        )
        renewable_by_time: dict[datetime, float] = defaultdict(float)
        for item in generation.intervals:
            renewable_by_time[item.interval_start_utc] += item.generation_mw
        intervals = [
            NetLoadInterval(
                interval_start=item.interval_start,
                load_mw=item.load_mw,
                renewable_generation_mw=renewable_by_time.get(
                    chronological_instant(item.interval_start), 0.0
                ),
                net_load_mw=item.load_mw
                - renewable_by_time.get(chronological_instant(item.interval_start), 0.0),
            )
            for item in loads.intervals
        ]
        values = [item.net_load_mw for item in intervals]
        return NetLoadResponse(
            intervals=intervals,
            average_net_load_mw=_mean(values),
            peak_net_load_mw=max(values) if values else None,
            minimum_net_load_mw=min(values) if values else None,
            metadata=_derived_meta(
                "Net Load Analysis",
                start,
                end,
                len(intervals),
                units={
                    "load_mw": "MW",
                    "renewable_generation_mw": "MW",
                    "net_load_mw": "MW",
                },
                completeness=_completeness(intervals),
            ),
            warnings=list(dict.fromkeys(loads.warnings + generation.warnings)),
        )

    async def analyze_supply_stack(self, request: SupplyStackRequest) -> SupplyStackResponse:
        response = await self._operations.get_energy_merit_order(
            DailyPageRequest(report_date=request.report_date, limit=request.limit),
            paginate=False,
        )
        blocks = [
            block
            for block in response.blocks
            if _hour_ending(block.interval_start) == request.hour_ending
        ]
        blocks.sort(key=lambda item: (item.block_price_cad_per_mwh or 0.0, item.asset_id or ""))
        offered = sum(
            item.block_size_mw
            if item.block_size_mw is not None
            else max((item.to_mw or 0.0) - (item.from_mw or 0.0), 0.0)
            for item in blocks
        )
        dispatched = sum(item.dispatched_mw or 0.0 for item in blocks)
        dispatched_prices = [
            item.block_price_cad_per_mwh
            for item in blocks
            if (item.dispatched_mw or 0.0) > 0 and item.block_price_cad_per_mwh is not None
        ]
        limited = blocks[: request.limit]
        warnings = list(response.warnings)
        if len(blocks) > request.limit:
            warnings.append(
                f"Returned the first {request.limit} of {len(blocks)} offer blocks; aggregate "
                "statistics use the complete hour."
            )
        return SupplyStackResponse(
            report_date=request.report_date,
            hour_ending=request.hour_ending,
            blocks=limited,
            offered_mw=offered,
            dispatched_mw=dispatched,
            marginal_offer_cad_per_mwh=max(dispatched_prices) if dispatched_prices else None,
            metadata=_derived_meta(
                "Supply Stack Analysis",
                None,
                None,
                len(blocks),
                units={
                    "offered_mw": "MW",
                    "dispatched_mw": "MW",
                    "marginal_offer_cad_per_mwh": "CAD/MWh",
                },
                completeness=_completeness(blocks),
                extra={"report_date": request.report_date.isoformat()},
            ),
            warnings=warnings,
        )

    async def analyze_intertie_utilization(
        self, request: IntertieUtilizationRequest
    ) -> IntertieUtilizationResponse:
        response = await self._operations.get_intertie_capability(
            IntertieCapabilityRequest(
                start_date=request.start_date,
                end_date=request.end_date,
                include_versions=False,
            ),
            paginate=False,
        )
        filters = {item.upper() for item in request.interties}
        groups: dict[tuple[str, str], list] = defaultdict(list)
        for item in response.intervals:
            if filters and item.intertie.upper() not in filters:
                continue
            groups[(item.intertie, item.direction)].append(item)
        results: list[IntertieUtilizationResult] = []
        for (intertie, direction), rows in sorted(groups.items()):
            capabilities = [
                item.available_transfer_capability_mw
                for item in rows
                if item.available_transfer_capability_mw is not None
            ]
            offers = [item.gross_offer_mw for item in rows if item.gross_offer_mw is not None]
            ratios = [
                item.gross_offer_mw / item.available_transfer_capability_mw
                for item in rows
                if item.gross_offer_mw is not None
                and item.available_transfer_capability_mw is not None
                and item.available_transfer_capability_mw > 0
            ]
            results.append(
                IntertieUtilizationResult(
                    intertie=intertie,
                    direction="import" if direction == "import" else "export",
                    observation_count=len(rows),
                    average_available_transfer_capability_mw=_mean(capabilities),
                    average_gross_offer_mw=_mean(offers),
                    average_offer_to_capability_ratio=_mean(ratios),
                    maximum_offer_to_capability_ratio=max(ratios) if ratios else None,
                    constrained_hours=sum(ratio >= 0.95 for ratio in ratios),
                )
            )
        return IntertieUtilizationResponse(
            results=results,
            methodology=(
                "Uses gross import/export offers divided by available transfer capability. "
                "This offer-to-capability ratio is a utilization proxy, not metered power flow."
            ),
            metadata=_derived_meta(
                "Intertie Offer-to-Capability Analysis",
                None,
                None,
                len(results),
                units={
                    "available_transfer_capability_mw": "MW",
                    "gross_offer_mw": "MW",
                    "offer_to_capability_ratio": "ratio",
                },
                completeness=_completeness(results),
                extra={
                    "start_date": request.start_date.isoformat(),
                    "end_date": request.end_date.isoformat(),
                },
            ),
            warnings=response.warnings,
        )

    async def analyze_generation_mix(
        self, request: GenerationAnalysisRequest
    ) -> GenerationMixResponse:
        start, end = validate_range(
            request.start, request.end, max_days=31, label="generation mix range"
        )
        response = await self._history.get_historical_generation(
            HistoricalGenerationRequest(
                start=start,
                end=end,
                interval="hourly",
                fuel_types=request.fuel_types,
            ),
            paginate=False,
        )
        groups: dict[str, list[float]] = defaultdict(list)
        for item in response.intervals:
            groups[item.fuel_type].append(item.generation_mw)
        totals = {fuel: sum(values) for fuel, values in groups.items()}
        total = sum(totals.values())
        results = [
            GenerationMixResult(
                fuel_type=fuel,
                generation_mwh=totals[fuel],
                share=totals[fuel] / total if total else None,
                average_generation_mw=mean(values),
                peak_generation_mw=max(values),
            )
            for fuel, values in sorted(groups.items())
        ]
        return GenerationMixResponse(
            results=results,
            total_generation_mwh=total,
            metadata=_derived_meta(
                "Historical Generation Mix Analysis",
                start,
                end,
                len(results),
                units={"generation_mwh": "MWh", "share": "ratio"},
                completeness=_completeness(results),
            ),
            warnings=response.warnings,
        )

    async def analyze_asset_dispatch(self, request: AssetDispatchRequest) -> AssetDispatchResponse:
        start, end = validate_range(
            request.start, request.end, max_days=31, label="asset dispatch range"
        )
        response = await self._history.get_historical_generation(
            HistoricalGenerationRequest(
                start=start,
                end=end,
                interval="hourly",
                asset_ids=request.asset_ids,
            ),
            paginate=False,
        )
        groups: dict[str, list] = defaultdict(list)
        for item in response.intervals:
            groups[item.asset_id].append(item)
        results: list[AssetDispatchResult] = []
        for asset_id, rows in sorted(groups.items()):
            rows.sort(key=lambda item: item.interval_start_utc)
            values = [item.generation_mw for item in rows]
            factors = [
                item.generation_mw / item.maximum_capability_mw
                for item in rows
                if item.maximum_capability_mw is not None and item.maximum_capability_mw > 0
            ]
            ramps = [right - left for left, right in pairwise(values)]
            results.append(
                AssetDispatchResult(
                    asset_id=asset_id,
                    fuel_type=rows[0].fuel_type,
                    observation_count=len(rows),
                    generation_mwh=sum(values),
                    average_generation_mw=mean(values),
                    peak_generation_mw=max(values),
                    average_capacity_factor=_mean(factors),
                    maximum_up_ramp_mw=max(ramps) if ramps else None,
                    maximum_down_ramp_mw=min(ramps) if ramps else None,
                )
            )
        return AssetDispatchResponse(
            results=results,
            metadata=_derived_meta(
                "Asset Dispatch Analysis",
                start,
                end,
                len(results),
                units={
                    "generation_mwh": "MWh",
                    "generation_mw": "MW",
                    "capacity_factor": "ratio",
                    "ramp_mw": "MW per hourly interval",
                },
                completeness=_completeness(results),
            ),
            warnings=response.warnings,
        )

    async def analyze_outage_impact(self, request: OutageImpactRequest) -> OutageImpactResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_load_days,
            label="outage impact range",
        )
        prices = await self._history.get_historical_pool_prices(
            PoolPriceRequest(start=start, end=end), paginate=False
        )
        outages = await self._grid.get_outages(OutagesRequest(start=start, end=end))
        price_by_time = {
            chronological_instant(item.interval_start): item.pool_price_cad_per_mwh
            for item in prices.intervals
        }
        pairs = [
            (item.total_outage_mw, price_by_time[chronological_instant(item.interval_start)])
            for item in outages.outages
            if chronological_instant(item.interval_start) in price_by_time
        ]
        threshold = request.high_outage_threshold_mw
        if threshold is None and pairs:
            threshold = _percentile([outage for outage, _ in pairs], 75)
        high_prices = [
            price for outage, price in pairs if threshold is not None and outage >= threshold
        ]
        other_prices = [
            price for outage, price in pairs if threshold is not None and outage < threshold
        ]
        high_average = _mean(high_prices)
        other_average = _mean(other_prices)
        return OutageImpactResponse(
            matched_observations=len(pairs),
            high_outage_observations=len(high_prices),
            threshold_mw=threshold,
            average_price_high_outage_cad_per_mwh=high_average,
            average_price_other_hours_cad_per_mwh=other_average,
            price_difference_cad_per_mwh=(
                high_average - other_average
                if high_average is not None and other_average is not None
                else None
            ),
            outage_price_correlation=_correlation(pairs),
            methodology=(
                "Matches hourly outage capacity to pool price. High-outage hours use the "
                "requested threshold or the sample 75th percentile. Difference and Pearson "
                "correlation are descriptive associations and do not establish causation."
            ),
            metadata=_derived_meta(
                "Generator Outage and Price Association",
                start,
                end,
                len(pairs),
                units={
                    "outage_capacity": "MW",
                    "pool_price": "CAD/MWh",
                    "correlation": "Pearson r",
                },
                completeness=_completeness(pairs),
            ),
            warnings=list(dict.fromkeys(prices.warnings + outages.warnings)),
        )

    async def analyze_forecast_error(self, request: ForecastErrorRequest) -> ForecastErrorResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_load_days,
            label="forecast error range",
        )
        response = await self._history.get_forecast(
            ForecastRequest(start=start, end=end, series=request.series, limit=2_000),
            paginate=False,
        )
        pairs = [
            (item.interval_start, item.actual_value, item.forecast_value)
            for item in response.intervals
            if item.forecast_value is not None
        ]
        errors = [forecast - actual for _, actual, forecast in pairs if forecast is not None]
        percentage_errors = [
            abs(forecast - actual) / abs(actual)
            for _, actual, forecast in pairs
            if forecast is not None and actual != 0
        ]
        by_hour_values: dict[int, list[float]] = defaultdict(list)
        for timestamp, actual, forecast in pairs:
            if forecast is not None:
                by_hour_values[to_market(timestamp).hour].append(forecast - actual)
        by_hour = [
            ForecastErrorByHour(
                market_hour=hour,
                observation_count=len(values),
                mean_error_mw=mean(values),
                mean_absolute_error_mw=mean(abs(value) for value in values),
                root_mean_squared_error_mw=math.sqrt(mean(value**2 for value in values)),
            )
            for hour, values in sorted(by_hour_values.items())
        ]
        warnings = list(response.warnings)
        if len(pairs) < len(response.intervals):
            warnings.append(
                f"Excluded {len(response.intervals) - len(pairs)} intervals without forecasts."
            )
        return ForecastErrorResponse(
            series=request.series,
            observation_count=len(errors),
            mean_error_mw=_mean(errors),
            mean_absolute_error_mw=(mean(abs(value) for value in errors) if errors else None),
            root_mean_squared_error_mw=(
                math.sqrt(mean(value**2 for value in errors)) if errors else None
            ),
            mean_absolute_percentage_error=_mean(percentage_errors),
            by_market_hour=by_hour,
            metadata=_derived_meta(
                "Forecast Error Analysis",
                start,
                end,
                len(errors),
                units={
                    "error": "MW (forecast minus actual)",
                    "mean_absolute_percentage_error": "ratio",
                },
                completeness=_completeness(errors),
            ),
            warnings=warnings,
        )


def _price_statistics_response(
    values: list[float],
    percentiles: list[float],
    start: datetime,
    end: datetime,
    *,
    warnings: list[str],
) -> PriceStatisticsResponse:
    return PriceStatisticsResponse(
        observation_count=len(values),
        average_cad_per_mwh=_mean(values),
        median_cad_per_mwh=median(values) if values else None,
        minimum_cad_per_mwh=min(values) if values else None,
        maximum_cad_per_mwh=max(values) if values else None,
        standard_deviation_cad_per_mwh=pstdev(values) if values else None,
        negative_price_hours=sum(value < 0 for value in values),
        hours_at_or_above_100_cad_per_mwh=sum(value >= 100 for value in values),
        percentiles=[
            PercentileValue(percentile=value, value=_percentile(values, value))
            for value in percentiles
        ]
        if values
        else [],
        metadata=_derived_meta(
            "Pool Price Statistics",
            start,
            end,
            len(values),
            units={"pool_price": "CAD/MWh"},
            completeness=_completeness(values),
        ),
        warnings=warnings,
    )


def _metric(name: str, focus: float | None, baseline: float | None, unit: str) -> ResearchMetric:
    change = focus - baseline if focus is not None and baseline is not None else None
    pct = (
        change / baseline if change is not None and baseline is not None and baseline != 0 else None
    )
    return ResearchMetric(
        name=name,
        focus_value=focus,
        baseline_value=baseline,
        absolute_change=change,
        percent_change=pct,
        unit=unit,
    )


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = percentile / 100 * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _correlation(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    xs = [item[0] for item in pairs]
    ys = [item[1] for item in pairs]
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    denominator = math.sqrt(sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys))
    return numerator / denominator if denominator else None


def _mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _hour_ending(value: datetime) -> int:
    hour = to_market(value).hour + 1
    return 24 if hour == 24 else hour


def _inclusive_market_dates(start: datetime, end: datetime) -> tuple[date, date]:
    return to_market(start).date(), to_market(to_utc(end) - timedelta(microseconds=1)).date()


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _completeness(values: Sequence[object]) -> DataCompleteness:
    return DataCompleteness.COMPLETE if values else DataCompleteness.EMPTY


def _derived_meta(
    dataset: str,
    start: datetime | None,
    end: datetime | None,
    count: int,
    *,
    units: dict[str, str],
    completeness: DataCompleteness,
    extra: dict[str, object] | None = None,
) -> DatasetMetadata:
    now = utc_now()
    return DatasetMetadata(
        dataset=dataset,
        source_product="Deterministic server-side calculation over AESO observations",
        retrieved_at=now,
        served_at=now,
        status=DataStatus.ACTUAL,
        observation_type=ObservationType.DERIVED,
        finality=FinalityStatus.UNKNOWN,
        completeness=completeness,
        available_series=[dataset] if count else [],
        units=units,
        request_start=start,
        request_end=end,
        provider=ProviderName.DERIVED,
        observation_count=count,
        extra=extra or {},
    )
