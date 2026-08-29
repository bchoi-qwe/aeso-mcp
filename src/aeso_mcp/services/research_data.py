# SPDX-License-Identifier: MIT
"""Research-data retrieval and bounded, descriptive analyses.

The provider owns source-specific parsing.  This service owns cache keys,
pagination, metadata, and the small intent-level joins that are useful to an
agent.  It deliberately keeps source observations separate from derived
summaries: missing participant mappings, price joins, and source gaps remain
visible in warnings and metadata rather than being treated as zero.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any

from aeso_mcp.config import Settings
from aeso_mcp.errors import AesoMcpError, InvalidDateRangeError, UnsupportedDatasetError
from aeso_mcp.models.assets import AssetsRequest
from aeso_mcp.models.common import (
    DataCompleteness,
    DatasetMetadata,
    DataStatus,
    FinalityStatus,
    ObservationType,
    ProviderName,
)
from aeso_mcp.models.operations import DailyPageRequest
from aeso_mcp.models.prices import PoolPriceRequest
from aeso_mcp.models.research_data import (
    CongestionAnalysisRequest,
    CongestionAnalysisResponse,
    CongestionAnalysisResult,
    EeaEventRecord,
    FrequencyAnalysisRequest,
    FrequencyAnalysisResponse,
    ParticipantConcentrationRequest,
    ParticipantConcentrationResponse,
    ParticipantConcentrationResult,
    PlanningAreaLoadGenerationInterval,
    RegionalAnalysisRequest,
    RegionalAnalysisResponse,
    RegionalAnalysisResult,
    ResearchDataRequest,
    ResearchDataResponse,
    ResearchDataset,
    ScarcityAnalysisRequest,
    ScarcityAnalysisResponse,
    SystemFrequencyInterval,
)
from aeso_mcp.providers.research_data import AesoResearchDataProvider
from aeso_mcp.services.cache import AsyncTTLCache, CacheInfo
from aeso_mcp.services.market import _meta, _paginate
from aeso_mcp.timeutil import (
    chronological_instant,
    elapsed_hours,
    to_market,
    utc_now,
    validate_range,
)


class ResearchDataService:
    """Serve official research archives and compact intent-level analyses."""

    def __init__(
        self,
        provider: AesoResearchDataProvider,
        settings: Settings,
        cache: AsyncTTLCache | None = None,
        *,
        market: Any | None = None,
        operations: Any | None = None,
        assets: Any | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._cache = cache or AsyncTTLCache(max_entries=settings.cache_max_entries)
        self._market = market
        self._operations = operations
        self._assets = assets

    async def get_research_data(
        self,
        request: ResearchDataRequest,
        *,
        paginate: bool = True,
    ) -> ResearchDataResponse:
        """Return a bounded page from one official research dataset."""
        if request.dataset == "system_frequency":
            raise UnsupportedDatasetError(
                "Raw system-frequency rows are not exposed; use analyze_system_frequency "
                "with a bounded six-hour window."
            )
        records, provenance, cache_info, start, end = await self._fetch(request)
        records = _sort_records(records)
        page, page_info = _paginate(records, request.offset, request.limit, paginate)
        status, observation_type, finality = _dataset_state(request.dataset)
        metadata = _dataset_metadata(
            dataset=_dataset_title(request.dataset),
            dataset_key=request.dataset,
            provenance=provenance,
            status=status,
            observation_type=observation_type,
            finality=finality,
            start=start,
            end=end,
            count=len(records),
            cache_info=cache_info,
        )
        return ResearchDataResponse(
            records=page,
            page=page_info,
            metadata=metadata,
            warnings=_provenance_warnings(provenance),
        )

    async def analyze_participant_concentration(
        self,
        request: ParticipantConcentrationRequest,
    ) -> ParticipantConcentrationResponse:
        """Summarize offered merit-order blocks by current participant mapping.

        The historical merit-order archive contains asset IDs, while the
        participant relationship is a current registry.  The join is therefore
        reported as a mapping-based concentration measure, not historical
        ownership or a causal market-power conclusion.
        """
        start, end = validate_range(
            request.start,
            request.end,
            max_days=31,
            label="participant concentration range",
        )
        if self._operations is None or self._assets is None:
            raise UnsupportedDatasetError(
                "Participant concentration requires configured operations and asset services."
            )

        participant_request = ResearchDataRequest(dataset="pool_participants", limit=2_000)
        participant_records, participant_prov, participant_cache, _, _ = await self._fetch(
            participant_request
        )
        participant_by_id = {
            _normal_id(record.pool_participant_id): record
            for record in participant_records
            if hasattr(record, "pool_participant_id")
        }
        asset_response = await self._assets.get_assets(AssetsRequest(limit=5_000))
        asset_map: dict[str, tuple[str, str | None]] = {}
        for asset in asset_response.assets:
            if asset.pool_participant_id:
                asset_map[_normal_id(asset.asset_id)] = (
                    asset.pool_participant_id,
                    asset.pool_participant_name,
                )

        aggregate: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"volume": 0.0, "assets": set(), "blocks": 0, "name": None}
        )
        matched_blocks = 0
        unmapped_blocks = 0
        missing_volume_blocks = 0
        source_metadata = [
            asset_response.metadata,
            _source_metadata_from_provenance(
                dataset="Pool Participant Registry",
                provenance=participant_prov,
                cache_info=participant_cache,
            ),
        ]

        for report_day in _market_dates(start, end):
            response = await self._operations.get_energy_merit_order(
                DailyPageRequest(report_date=report_day, limit=2_000),
                paginate=False,
            )
            source_metadata.append(response.metadata)
            for block in response.blocks:
                if not _in_range(block.interval_start, start, end):
                    continue
                asset_id = _normal_id(block.asset_id) if block.asset_id else None
                mapping = asset_map.get(asset_id) if asset_id else None
                if mapping is None:
                    unmapped_blocks += 1
                    continue
                participant_id, asset_name = mapping
                matched_blocks += 1
                participant = participant_by_id.get(_normal_id(participant_id))
                name = participant.pool_participant_name if participant is not None else asset_name
                item = aggregate[participant_id]
                item["name"] = name or item["name"]
                if asset_id:
                    item["assets"].add(asset_id)
                item["blocks"] += 1
                volume = _offered_block_volume(block)
                if volume is None:
                    missing_volume_blocks += 1
                elif volume >= 0:
                    item["volume"] += volume
                else:
                    missing_volume_blocks += 1

        ordered = sorted(
            aggregate.items(),
            key=lambda item: (-float(item[1]["volume"]), item[0]),
        )
        total_volume = sum(float(item[1]["volume"]) for item in ordered)
        all_shares = [
            (float(item["volume"]) / total_volume) if total_volume else 0.0 for _, item in ordered
        ]
        results = [
            ParticipantConcentrationResult(
                pool_participant_id=participant_id,
                pool_participant_name=item["name"],
                offered_volume_mw=float(item["volume"]),
                offered_volume_share=(
                    float(item["volume"]) / total_volume if total_volume else None
                ),
                asset_count=len(item["assets"]),
                block_count=int(item["blocks"]),
            )
            for (participant_id, item) in ordered[: request.top_n]
        ]
        warnings: list[str] = []
        if unmapped_blocks:
            warnings.append(
                f"{unmapped_blocks} historical merit-order block(s) had no current asset/participant mapping."
            )
        if missing_volume_blocks:
            warnings.append(
                f"{missing_volume_blocks} mapped block(s) had no non-negative offered-volume field."
            )
        if getattr(asset_response, "truncated", False):
            warnings.append("The current asset registry was truncated at 5,000 records.")
        if not participant_records:
            warnings.append("The current Pool Participant registry returned no records.")
        metadata = _derived_metadata(
            dataset="Participant Concentration Analysis",
            start=start,
            end=end,
            count=len(aggregate),
            units={"offered_volume_mw": "MW", "offered_volume_share": "ratio"},
            granularity="historical merit-order block aggregate",
            source_metadata=source_metadata,
            extra={
                "mapping_basis": "current AESO asset and Pool Participant registries",
                "historical_ownership_inference": False,
            },
            completeness=(
                DataCompleteness.EMPTY
                if not aggregate
                else DataCompleteness.DEGRADED
                if unmapped_blocks or missing_volume_blocks
                else DataCompleteness.COMPLETE
            ),
        )
        return ParticipantConcentrationResponse(
            results=results,
            total_offered_volume_mw=total_volume,
            hhi=(sum(share * share for share in all_shares) if total_volume else None),
            top_three_share=(sum(all_shares[:3]) if total_volume else None),
            top_five_share=(sum(all_shares[:5]) if total_volume else None),
            matched_block_count=matched_blocks,
            unmapped_block_count=unmapped_blocks,
            methodology=(
                "Sums non-negative historical merit-order block_size_mw, or to_mw-from_mw "
                "when block_size_mw is absent, after joining asset IDs to the current AESO "
                "asset/Pool Participant registries. HHI and shares describe this mapped offer "
                "sample; they do not establish historical ownership, dispatch, market power, "
                "or causality."
            ),
            metadata=metadata,
            warnings=warnings,
        )

    async def analyze_regional(
        self,
        request: RegionalAnalysisRequest,
    ) -> RegionalAnalysisResponse:
        """Aggregate official planning-area load and generation observations."""
        start, end = validate_range(
            request.start, request.end, max_days=366, label="regional range"
        )
        source_request = ResearchDataRequest(
            dataset="planning_area",
            start=start,
            end=end,
            planning_area=request.planning_area,
            region=request.region,
            limit=2_000,
        )
        records, provenance, cache_info, _, _ = await self._fetch(source_request)
        groups: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for record in records:
            if not isinstance(record, PlanningAreaLoadGenerationInterval):
                continue
            key = (record.region, record.planning_area)
            group = groups[key]
            group["count"] += 1
            for field in (
                "load_mw",
                "system_generation_mw",
                "csd_generation_mw",
                "behind_the_fence_generation_mw",
                "actual_load_mw",
            ):
                value = getattr(record, field)
                if value is not None:
                    group[field] += float(value)
                    group[f"{field}_count"] += 1
            if record.actual_load_mw is not None:
                group["peak_actual_load_mw"] = max(
                    group.get("peak_actual_load_mw", float("-inf")),
                    record.actual_load_mw,
                )
                group["total_actual_load_mwh"] += record.actual_load_mw * max(
                    elapsed_hours(record.interval_start, record.interval_end),
                    0.0,
                )
        ordered = sorted(groups.items(), key=lambda item: (item[0][0], item[0][1]))
        results = [
            RegionalAnalysisResult(
                region=region,
                planning_area=planning_area,
                observation_count=int(group["count"]),
                average_load_mw=_average(group, "load_mw"),
                average_system_generation_mw=_average(group, "system_generation_mw"),
                average_csd_generation_mw=_average(group, "csd_generation_mw"),
                average_behind_the_fence_generation_mw=_average(
                    group, "behind_the_fence_generation_mw"
                ),
                average_actual_load_mw=_average(group, "actual_load_mw"),
                peak_actual_load_mw=group.get("peak_actual_load_mw"),
                total_actual_load_mwh=(
                    group["total_actual_load_mwh"] if group.get("actual_load_mw_count", 0) else None
                ),
            )
            for (region, planning_area), group in ordered[: request.limit]
        ]
        warnings = (
            [f"Results were truncated to {request.limit} planning-area groups."]
            if len(ordered) > request.limit
            else []
        )
        return RegionalAnalysisResponse(
            results=results,
            methodology=(
                "Groups AESO hourly planning-area rows by the source REGION and PLANNING_AREA. "
                "Average fields are arithmetic means of observed MW values; total_actual_load_mwh "
                "uses each source interval's elapsed duration. No regional cause or unobserved "
                "area is inferred."
            ),
            metadata=_derived_metadata(
                dataset="Regional Load and Generation Analysis",
                start=start,
                end=end,
                count=len(groups),
                units={"load_mw": "MW", "generation_mw": "MW", "total_actual_load_mwh": "MWh"},
                granularity="hourly planning-area aggregate",
                source_metadata=[
                    _source_metadata_from_provenance(
                        dataset="Planning Area - Hourly Load and Generation",
                        provenance=provenance,
                        cache_info=cache_info,
                    )
                ],
                completeness=(
                    DataCompleteness.EMPTY
                    if not groups
                    else DataCompleteness.DEGRADED
                    if any(
                        group.get(f"{field}_count", 0) < group["count"]
                        for group in groups.values()
                        for field in (
                            "load_mw",
                            "system_generation_mw",
                            "csd_generation_mw",
                            "behind_the_fence_generation_mw",
                            "actual_load_mw",
                        )
                    )
                    else DataCompleteness.COMPLETE
                ),
            ),
            warnings=warnings,
        )

    async def analyze_regional_load_generation(
        self,
        request: RegionalAnalysisRequest,
    ) -> RegionalAnalysisResponse:
        """Named alias matching the MCP intent description."""
        return await self.analyze_regional(request)

    async def analyze_congestion(
        self,
        request: CongestionAnalysisRequest,
    ) -> CongestionAnalysisResponse:
        """Summarize constrained volume, optionally joined to pool price."""
        start, end = validate_range(
            request.start, request.end, max_days=366, label="congestion range"
        )
        source_request = ResearchDataRequest(
            dataset="constrained_volume",
            start=start,
            end=end,
            planning_area=request.planning_area,
            fuel_type=request.fuel_type,
            limit=2_000,
        )
        records, provenance, cache_info, _, _ = await self._fetch(source_request)
        price_by_instant: dict[datetime, float] = {}
        source_metadata = [
            _source_metadata_from_provenance(
                dataset="Constrained Volume by Planning Area and Fuel Type",
                provenance=provenance,
                cache_info=cache_info,
            )
        ]
        warnings: list[str] = []
        if request.include_pool_price:
            if self._market is None:
                warnings.append(
                    "Pool price join was requested but the market service is not configured."
                )
            else:
                try:
                    prices = await self._market.get_pool_prices(
                        PoolPriceRequest(start=start, end=end, limit=2_000),
                        paginate=False,
                    )
                except AesoMcpError as exc:
                    warnings.append(f"Pool price join was unavailable: {exc.to_client_message()}")
                else:
                    source_metadata.append(prices.metadata)
                    price_by_instant = {
                        chronological_instant(item.interval_start): item.pool_price_cad_per_mwh
                        for item in prices.intervals
                    }

        groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "volume": 0.0, "minutes": 0, "prices": []}
        )
        for record in records:
            key = (record.planning_area, record.fuel_type)
            group = groups[key]
            group["count"] += 1
            group["volume"] += record.constrained_volume_mwh
            group["minutes"] += record.constrained_minutes
            price = price_by_instant.get(chronological_instant(record.interval_start))
            if price is not None:
                group["prices"].append(price)
        ordered = sorted(
            groups.items(),
            key=lambda item: (-float(item[1]["volume"]), item[0][0], item[0][1]),
        )
        results = [
            CongestionAnalysisResult(
                planning_area=planning_area,
                fuel_type=fuel_type,
                constrained_observations=int(group["count"]),
                constrained_volume_mwh=float(group["volume"]),
                constrained_minutes=int(group["minutes"]),
                average_pool_price_cad_per_mwh=(mean(group["prices"]) if group["prices"] else None),
                matched_pool_price_observations=len(group["prices"]),
            )
            for (planning_area, fuel_type), group in ordered[: request.limit]
        ]
        matched_prices = sum(len(group["prices"]) for _, group in ordered)
        if request.include_pool_price and records and matched_prices < len(records):
            warnings.append(
                f"Pool prices matched {matched_prices} of {len(records)} constrained observations; "
                "unmatched prices remain null."
            )
        if len(ordered) > request.limit:
            warnings.append(f"Results were truncated to {request.limit} planning-area/fuel groups.")
        return CongestionAnalysisResponse(
            results=results,
            methodology=(
                "Sums AESO Constrained_MW and Minutes by source planning area and fuel type. "
                "When requested, Pool Price is joined by the exact UTC hourly interval and "
                "averaged only over matched observations; this is a descriptive congestion/price "
                "association, not a causal attribution."
            ),
            metadata=_derived_metadata(
                dataset="Constrained Volume Analysis",
                start=start,
                end=end,
                count=len(groups),
                units={
                    "constrained_volume_mwh": "MWh",
                    "constrained_minutes": "minutes",
                    "pool_price_cad_per_mwh": "CAD/MWh",
                },
                granularity="hourly constrained-volume aggregate",
                source_metadata=source_metadata,
                completeness=(
                    DataCompleteness.EMPTY
                    if not groups
                    else DataCompleteness.DEGRADED
                    if request.include_pool_price and matched_prices < len(records)
                    else DataCompleteness.COMPLETE
                ),
            ),
            warnings=warnings,
        )

    async def analyze_constrained_volume(
        self,
        request: CongestionAnalysisRequest,
    ) -> CongestionAnalysisResponse:
        """Named alias matching the MCP intent description."""
        return await self.analyze_congestion(request)

    async def analyze_scarcity(
        self,
        request: ScarcityAnalysisRequest,
    ) -> ScarcityAnalysisResponse:
        """Combine categorical adequacy/cushion, EEA, and optional price context."""
        start, end = validate_range(
            request.start, request.end, max_days=366, label="scarcity range"
        )
        source_requests = [
            ResearchDataRequest(dataset="supply_adequacy", start=start, end=end, limit=2_000),
            ResearchDataRequest(dataset="supply_cushion", start=start, end=end, limit=2_000),
            ResearchDataRequest(dataset="eea_events", start=start, end=end, limit=2_000),
        ]
        fetched = [await self._fetch(source_request) for source_request in source_requests]
        adequacy, adequacy_prov, adequacy_cache, _, _ = fetched[0]
        cushion, cushion_prov, cushion_cache, _, _ = fetched[1]
        eea, eea_prov, eea_cache, _, _ = fetched[2]
        adequacy_counts = Counter(
            record.web_code for record in adequacy if hasattr(record, "web_code")
        )
        cushion_counts = Counter(
            record.web_code for record in cushion if hasattr(record, "web_code")
        )
        eea_records = [record for record in eea if isinstance(record, EeaEventRecord)]
        level_counts = Counter(record.eea_level for record in eea_records)
        source_metadata = [
            _source_metadata_from_provenance(
                dataset="Historical Supply Adequacy Web Codes",
                provenance=adequacy_prov,
                cache_info=adequacy_cache,
            ),
            _source_metadata_from_provenance(
                dataset="Historical Supply Cushion Web Codes",
                provenance=cushion_prov,
                cache_info=cushion_cache,
            ),
            _source_metadata_from_provenance(
                dataset="Historical Energy Emergency Alert Event Data",
                provenance=eea_prov,
                cache_info=eea_cache,
            ),
        ]
        warnings: list[str] = []
        price_values: list[float] = []
        if self._market is None:
            warnings.append(
                "Pool price context was not available because the market service is not configured."
            )
        else:
            try:
                prices = await self._market.get_pool_prices(
                    PoolPriceRequest(start=start, end=end, limit=2_000),
                    paginate=False,
                )
            except AesoMcpError as exc:
                warnings.append(f"Pool price context was unavailable: {exc.to_client_message()}")
            else:
                source_metadata.append(prices.metadata)
                price_values = [item.pool_price_cad_per_mwh for item in prices.intervals]
        high_prices = [
            price for price in price_values if price >= request.high_price_threshold_cad_per_mwh
        ]
        if not cushion:
            warnings.append(
                "The published historical Supply Cushion archive begins in June 2024; no cushion "
                "observations were available for this range."
            )
        return ScarcityAnalysisResponse(
            observation_count=max(len(adequacy), len(cushion)),
            adequacy_code_counts=dict(sorted(adequacy_counts.items())),
            cushion_code_counts=dict(sorted(cushion_counts.items())),
            eea_event_count=len(eea_records),
            eea_level_counts=dict(sorted(level_counts.items())),
            high_price_observation_count=len(high_prices),
            average_pool_price_cad_per_mwh=(mean(price_values) if price_values else None),
            methodology=(
                "Counts AESO-published categorical Supply Adequacy and Supply Cushion web codes "
                "and EEA event levels in the requested half-open range. Optional Pool Price "
                "context is joined as a threshold count/mean only; web codes are not converted to "
                "numeric reserve or cushion values and no scarcity cause is inferred."
            ),
            metadata=_derived_metadata(
                dataset="Scarcity Context Analysis",
                start=start,
                end=end,
                count=max(len(adequacy), len(cushion), len(eea_records)),
                units={"pool_price_cad_per_mwh": "CAD/MWh"},
                granularity="hourly status and event context",
                source_metadata=source_metadata,
                completeness=(
                    DataCompleteness.EMPTY
                    if not max(len(adequacy), len(cushion), len(eea_records))
                    else DataCompleteness.DEGRADED
                    if not adequacy or not cushion
                    else DataCompleteness.COMPLETE
                ),
            ),
            warnings=warnings,
        )

    async def analyze_system_frequency(
        self,
        request: FrequencyAnalysisRequest,
    ) -> FrequencyAnalysisResponse:
        """Compute statistics from a server-side bounded 10-second frequency window."""
        start, end = validate_range(request.start, request.end, max_days=1, label="frequency range")
        if (chronological_instant(end) - chronological_instant(start)) > timedelta(hours=6):
            raise InvalidDateRangeError("System frequency raw windows may not exceed 6 hours.")
        source_request = ResearchDataRequest(
            dataset="system_frequency",
            start=start,
            end=end,
            frequency_resolution="10s",
            limit=2_000,
        )
        records, provenance, cache_info, _, _ = await self._fetch(source_request)
        rows = [record for record in records if isinstance(record, SystemFrequencyInterval)]
        averages = [row.average_frequency_hz for row in rows]
        expected = max(
            1,
            round((chronological_instant(end) - chronological_instant(start)).total_seconds() / 10),
        )
        below = sum(1 for row in rows if row.minimum_frequency_hz < request.lower_threshold_hz)
        above = sum(1 for row in rows if row.maximum_frequency_hz > request.upper_threshold_hz)
        warnings: list[str] = []
        if len(rows) < expected:
            warnings.append(
                f"The source window contained {len(rows)} of approximately {expected} expected 10-second observations."
            )
        ordered = sorted(averages)
        return FrequencyAnalysisResponse(
            observation_count=len(rows),
            average_frequency_hz=(mean(averages) if averages else None),
            minimum_frequency_hz=(min(row.minimum_frequency_hz for row in rows) if rows else None),
            maximum_frequency_hz=(max(row.maximum_frequency_hz for row in rows) if rows else None),
            p05_frequency_hz=_percentile(ordered, 0.05),
            p95_frequency_hz=_percentile(ordered, 0.95),
            flagged_interval_seconds_below_lower_threshold=below * 10.0,
            flagged_interval_seconds_above_upper_threshold=above * 10.0,
            observations_below_lower_threshold=below,
            observations_above_upper_threshold=above,
            methodology=(
                "Uses the AESO 10-second average/minimum/maximum frequency fields. Flagged interval "
                "exposure counts 10 seconds when a source minimum is below the lower threshold or "
                "source maximum is above the upper threshold; it is an interval exposure proxy and "
                "upper bound, not exact time outside the threshold. Missing source intervals are not "
                "filled and no event cause is inferred. Raw retrieval is server-side and bounded "
                "to six elapsed hours."
            ),
            metadata=_derived_metadata(
                dataset="System Frequency Analysis",
                start=start,
                end=end,
                count=len(rows),
                units={"frequency_hz": "Hz", "duration": "seconds"},
                granularity="10-second frequency window",
                source_metadata=[
                    _source_metadata_from_provenance(
                        dataset="System Frequency (10-second)",
                        provenance=provenance,
                        cache_info=cache_info,
                        expected_observations=expected,
                    )
                ],
                completeness=(
                    DataCompleteness.EMPTY
                    if not rows
                    else DataCompleteness.PARTIAL
                    if len(rows) < expected
                    else DataCompleteness.COMPLETE
                ),
            ),
            warnings=warnings,
        )

    async def _fetch(
        self,
        request: ResearchDataRequest,
    ) -> tuple[list[Any], dict[str, object], CacheInfo, datetime | None, datetime | None]:
        start, end = _normalized_request_range(request)
        normalized = request
        if start is not None and end is not None:
            normalized = request.model_copy(update={"start": start, "end": end})
        key = (
            "research_data",
            normalized.dataset,
            json.dumps(
                normalized.model_dump(exclude={"offset", "limit"}),
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ),
        )
        ttl_s = (
            self._settings.cache_ttl_assets_s
            if normalized.dataset == "pool_participants"
            else self._settings.cache_ttl_historical_public_report_s
        )
        cached = await self._cache.get_or_set_with_metadata(
            key,
            lambda: self._provider.get_research_data(normalized),
            ttl_s=ttl_s,
        )
        records, provenance = cached.value
        return list(records), provenance, cached.info, start, end


def _normalized_request_range(
    request: ResearchDataRequest,
) -> tuple[datetime | None, datetime | None]:
    if request.start is None or request.end is None:
        return None, None
    max_days = 1 if request.dataset == "system_frequency" else 366
    start, end = validate_range(
        request.start, request.end, max_days=max_days, label=f"{request.dataset} range"
    )
    if request.dataset == "system_frequency" and elapsed_hours(start, end) > 6:
        raise InvalidDateRangeError("System frequency raw windows may not exceed 6 hours.")
    return start, end


def _dataset_title(dataset: ResearchDataset) -> str:
    return {
        "supply_adequacy": "Historical Supply Adequacy Web Codes",
        "supply_cushion": "Historical Supply Cushion Web Codes",
        "transmission_outages": "Historical Transmission Outages",
        "planning_area": "Planning Area - Hourly Load and Generation",
        "constrained_volume": "Constrained Volume by Planning Area and Fuel Type",
        "eea_events": "Historical Energy Emergency Alert Event Data",
        "or_directives": "Aggregated Contingency Reserve Directive Quantities",
        "system_frequency": "System Frequency (10-second)",
        "pool_participants": "Pool Participant Registry",
    }[dataset]


def _dataset_state(
    dataset: ResearchDataset,
) -> tuple[DataStatus, ObservationType, FinalityStatus]:
    if dataset in {"supply_adequacy", "supply_cushion"}:
        return DataStatus.FORECAST, ObservationType.FORECAST, FinalityStatus.FINAL
    if dataset == "pool_participants":
        return DataStatus.ACTUAL, ObservationType.ACTUAL, FinalityStatus.UNKNOWN
    return DataStatus.ACTUAL, ObservationType.ACTUAL, FinalityStatus.FINAL


def _dataset_metadata(
    *,
    dataset: str,
    dataset_key: ResearchDataset,
    provenance: Mapping[str, object],
    status: DataStatus,
    observation_type: ObservationType,
    finality: FinalityStatus,
    start: datetime | None,
    end: datetime | None,
    count: int,
    cache_info: CacheInfo,
) -> DatasetMetadata:
    expected = None
    if (
        start is not None
        and end is not None
        and dataset_key in {"supply_adequacy", "supply_cushion"}
    ):
        expected = max(1, round(elapsed_hours(start, end)))
    if start is not None and end is not None and dataset_key == "system_frequency":
        expected = max(1, round(elapsed_hours(start, end) * 360))
    return _meta(
        dataset=dataset,
        prov=provenance,
        status=status,
        observation_type=observation_type,
        finality=finality,
        units=_dataset_units(dataset_key),
        granularity=_dataset_granularity(dataset_key),
        start=start,
        end=end,
        count=count,
        cache_info=cache_info,
        publication_time=_publication_time(provenance),
        available_series=[dataset_key] if count else [],
        expected_observations=expected,
        completeness=(DataCompleteness.EMPTY if not count else None),
    )


def _source_metadata_from_provenance(
    *,
    dataset: str,
    provenance: Mapping[str, object],
    cache_info: CacheInfo,
    expected_observations: int | None = None,
) -> DatasetMetadata:
    return _meta(
        dataset=dataset,
        prov=provenance,
        status=DataStatus.ACTUAL,
        observation_type=ObservationType.ACTUAL,
        finality=FinalityStatus.FINAL,
        units={},
        granularity=None,
        count=None,
        cache_info=cache_info,
        publication_time=_publication_time(provenance),
        expected_observations=expected_observations,
    )


def _derived_metadata(
    *,
    dataset: str,
    start: datetime,
    end: datetime,
    count: int,
    units: dict[str, str],
    granularity: str,
    source_metadata: Sequence[DatasetMetadata],
    extra: Mapping[str, object] | None = None,
    completeness: DataCompleteness | None = None,
) -> DatasetMetadata:
    retrieved_at = max(
        (metadata.retrieved_at for metadata in source_metadata),
        default=utc_now(),
    )
    publication_times = [
        metadata.publication_time
        for metadata in source_metadata
        if metadata.publication_time is not None
    ]
    source_products = [
        metadata.source_product
        for metadata in source_metadata
        if metadata.source_product is not None
    ]
    provenance: dict[str, object] = {
        "provider": ProviderName.DERIVED.value,
        "source_product": dataset,
        "source_metadata": [metadata.model_dump(mode="json") for metadata in source_metadata],
    }
    if source_products:
        provenance["source_products"] = source_products
    if extra:
        provenance.update(extra)
    return _meta(
        dataset=dataset,
        prov=provenance,
        status=DataStatus.ACTUAL,
        observation_type=ObservationType.DERIVED,
        finality=FinalityStatus.FINAL,
        units=units,
        granularity=granularity,
        start=start,
        end=end,
        count=count,
        retrieved_at=retrieved_at,
        served_at=utc_now(),
        publication_time=max(publication_times, default=None),
        completeness=completeness
        or (DataCompleteness.EMPTY if count == 0 else DataCompleteness.COMPLETE),
    )


def _dataset_units(dataset: ResearchDataset) -> dict[str, str]:
    return {
        "supply_adequacy": {"web_code": "code"},
        "supply_cushion": {"web_code": "code"},
        "transmission_outages": {},
        "planning_area": {
            "load_mw": "MW",
            "system_generation_mw": "MW",
            "csd_generation_mw": "MW",
            "behind_the_fence_generation_mw": "MW",
            "actual_load_mw": "MW",
        },
        "constrained_volume": {"constrained_volume_mwh": "MWh", "constrained_minutes": "minutes"},
        "eea_events": {"duration_hours": "hours", "duration_minutes": "minutes"},
        "or_directives": {
            "time_weighted_average_capacity_mw": "MW",
            "capacity_mw": "MW",
            "time_weighted_average_directive_mw": "MW",
            "maximum_directive_mw": "MW",
            "energy_mwh": "MWh",
            "event_duration_seconds": "seconds",
        },
        "system_frequency": {
            "average_frequency_hz": "Hz",
            "maximum_frequency_hz": "Hz",
            "minimum_frequency_hz": "Hz",
        },
        "pool_participants": {},
    }[dataset]


def _dataset_granularity(dataset: ResearchDataset) -> str:
    return {
        "supply_adequacy": "1h",
        "supply_cushion": "1h",
        "transmission_outages": "outage interval",
        "planning_area": "1h",
        "constrained_volume": "1h",
        "eea_events": "event interval",
        "or_directives": "directive event interval",
        "system_frequency": "10s",
        "pool_participants": "current registry",
    }[dataset]


def _publication_time(provenance: Mapping[str, object]) -> datetime | None:
    value = provenance.get("source_publication_date")
    if not isinstance(value, str):
        return None
    try:
        return to_market(datetime.fromisoformat(value))
    except ValueError:
        return None


def _provenance_warnings(provenance: Mapping[str, object]) -> list[str]:
    warnings: list[str] = []
    skipped = provenance.get("source_unbounded_rows")
    if isinstance(skipped, int) and skipped:
        warnings.append(
            f"{skipped} source outage row(s) had no time bounds and were excluded from observations."
        )
    return warnings


def _sort_records(records: Sequence[Any]) -> list[Any]:
    return sorted(
        records,
        key=lambda record: (
            chronological_instant(record.interval_start)
            if hasattr(record, "interval_start")
            else datetime.min,
            getattr(record, "pool_participant_id", ""),
        ),
    )


def _normal_id(value: str) -> str:
    return value.strip().casefold()


def _offered_block_volume(block: Any) -> float | None:
    if block.block_size_mw is not None:
        return float(block.block_size_mw)
    if block.from_mw is not None and block.to_mw is not None:
        return float(block.to_mw) - float(block.from_mw)
    return None


def _average(group: Mapping[str, float], field: str) -> float | None:
    count = group.get(f"{field}_count", 0.0)
    return group[field] / count if count else None


def _market_dates(start: datetime, end: datetime) -> list[date]:
    first = to_market(start).date()
    last = to_market(end - timedelta(microseconds=1)).date()
    result: list[date] = []
    current = first
    while current <= last:
        result.append(current)
        current += timedelta(days=1)
    return result


def _in_range(value: datetime, start: datetime, end: datetime) -> bool:
    instant = chronological_instant(value)
    return chronological_instant(start) <= instant < chronological_instant(end)


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return float(values[lower] + (values[upper] - values[lower]) * weight)


__all__ = ["ResearchDataService"]
