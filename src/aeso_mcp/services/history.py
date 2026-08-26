# SPDX-License-Identifier: MIT
"""Historical archive retrieval, optional storage, and public research reports."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

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
from aeso_mcp.models.generation import LoadRequest
from aeso_mcp.models.history import (
    ForecastInterval,
    ForecastRequest,
    ForecastResponse,
    HistoricalDataset,
    HistoricalGenerationRequest,
    HistoricalGenerationResponse,
    HistoricalStoreStatusResponse,
    HistoricalStoreSyncRequest,
    HistoricalStoreSyncResponse,
    HistoricalSyncDatasetResult,
    UnitCommitmentSettlementRequest,
    UnitCommitmentSettlementResponse,
)
from aeso_mcp.models.operations import PageInfo
from aeso_mcp.models.prices import PoolPriceRequest
from aeso_mcp.providers.historical_generation import HistoricalGenerationProvider
from aeso_mcp.providers.public_reports import AesoPublicReportsProvider
from aeso_mcp.services.market import MarketService
from aeso_mcp.storage.history import HistoricalStore
from aeso_mcp.timeutil import start_of_market_day, to_market, to_utc, utc_now, validate_range


class HistoryService:
    """Typed historical-data boundary with explicit local persistence semantics."""

    def __init__(
        self,
        historical_generation: HistoricalGenerationProvider,
        public_reports: AesoPublicReportsProvider,
        market: MarketService,
        settings: Settings,
    ) -> None:
        self._generation = historical_generation
        self._public_reports = public_reports
        self._market = market
        self._settings = settings
        self._store = HistoricalStore(settings.history_store_path)

    async def get_historical_generation(
        self, request: HistoricalGenerationRequest, *, paginate: bool = True
    ) -> HistoricalGenerationResponse:
        max_days = 366 if request.interval == "hourly" else 7
        start, end = validate_range(
            request.start,
            request.end,
            max_days=max_days,
            label=f"{request.interval} historical generation range",
        )
        sources = [
            item
            for item in await self._generation.list_source_files(request.interval)
            if item.intersects(start, end)
        ]
        warnings: list[str] = []
        if not sources:
            return HistoricalGenerationResponse(
                intervals=[],
                page=_page(0, request.offset, request.limit),
                metadata=_history_meta(
                    "Historical CSD Generation",
                    start=start,
                    end=end,
                    count=0,
                    granularity=request.interval,
                    completeness=DataCompleteness.EMPTY,
                ),
                warnings=["No official CSD archive file intersects the requested range."],
            )

        if self._store.dependencies_available() and not request.refresh:
            current = all(
                self._store.source_is_current(
                    HistoricalDataset.HISTORICAL_GENERATION,
                    source.file_id,
                    source.source_hash,
                )
                for source in sources
            )
            if current:
                query_limit = request.limit if paginate else 10_000_000
                query_offset = request.offset if paginate else 0
                records, total = self._store.query_generation(
                    start=to_utc(start),
                    end=to_utc(end),
                    resolution=request.interval,
                    asset_ids=request.asset_ids,
                    fuel_types=request.fuel_types,
                    offset=query_offset,
                    limit=query_limit,
                )
                return HistoricalGenerationResponse(
                    intervals=records,
                    page=_page(
                        total,
                        request.offset if paginate else 0,
                        request.limit if paginate else max(total, 1),
                    ),
                    metadata=_history_meta(
                        "Historical CSD Generation",
                        start=start,
                        end=end,
                        count=total,
                        granularity=request.interval,
                        completeness=(
                            DataCompleteness.COMPLETE if total else DataCompleteness.EMPTY
                        ),
                        extra={"storage": "duckdb", "source_file_count": len(sources)},
                    ),
                )

        all_records = []
        for source in sources:
            records, _digest = await self._generation.fetch_source_file(
                source,
                interval=request.interval,
                start=start,
                end=end,
                asset_ids=set(request.asset_ids) or None,
                fuel_types=set(request.fuel_types) or None,
            )
            all_records.extend(records)
        all_records.sort(key=lambda item: (item.interval_start_utc, item.asset_id))
        total = len(all_records)
        page_records = (
            all_records[request.offset : request.offset + request.limit]
            if paginate
            else all_records
        )
        if self._store.dependencies_available():
            warnings.append(
                "Results came directly from the official archive because the local store was "
                "missing or stale; call sync_historical_store to persist complete source files."
            )
        else:
            warnings.append(
                "Results came directly from the official archive; install the analytics extra "
                "to enable incremental DuckDB/Parquet storage."
            )
        return HistoricalGenerationResponse(
            intervals=page_records,
            page=_page(
                total,
                request.offset if paginate else 0,
                request.limit if paginate else max(total, 1),
            ),
            metadata=_history_meta(
                "Historical CSD Generation",
                start=start,
                end=end,
                count=total,
                granularity=request.interval,
                completeness=DataCompleteness.COMPLETE if total else DataCompleteness.EMPTY,
                extra={"storage": "direct", "source_file_count": len(sources)},
            ),
            warnings=warnings,
        )

    async def sync_historical_store(
        self, request: HistoricalStoreSyncRequest
    ) -> HistoricalStoreSyncResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=90 if HistoricalDataset.LOAD in request.datasets else 366,
            label="historical store sync range",
        )
        self._store.initialize()
        results: list[HistoricalSyncDatasetResult] = []
        warnings: list[str] = []
        for dataset in request.datasets:
            if dataset == HistoricalDataset.HISTORICAL_GENERATION:
                results.append(await self._sync_generation(request, start, end))
            elif dataset == HistoricalDataset.POOL_PRICE:
                ranges = (
                    [(to_utc(start), to_utc(end))]
                    if request.refresh
                    else self._store.missing_market_ranges("pool_price", start, end)
                )
                received = written = partitions = duplicates = 0
                for range_start, range_end in ranges:
                    response = await self._market.get_pool_prices(
                        PoolPriceRequest(start=range_start, end=range_end), paginate=False
                    )
                    rows = [
                        {
                            "interval_start": item.interval_start,
                            "interval_end": item.interval_end,
                            "series": "pool_price",
                            "actual_value": item.pool_price_cad_per_mwh,
                            "forecast_value": item.forecast_pool_price_cad_per_mwh,
                            "unit": "CAD/MWh",
                            "source_product": response.metadata.source_product
                            or "Pool Price Report",
                            "source_retrieved_at": response.metadata.retrieved_at,
                            "observation_type": response.metadata.observation_type.value,
                            "finality": response.metadata.finality.value,
                            "completeness": response.metadata.completeness.value,
                        }
                        for item in response.intervals
                    ]
                    received += len(rows)
                    duplicates += len(rows) - len(
                        {to_utc(item.interval_start) for item in response.intervals}
                    )
                    count, rebuilt = self._store.upsert_market_series(dataset, rows)
                    written += count
                    partitions += rebuilt
                    warnings.extend(response.warnings)
                missing = _missing_count(
                    self._store.missing_market_ranges("pool_price", start, end)
                )
                results.append(
                    HistoricalSyncDatasetResult(
                        dataset=dataset,
                        records_received=received,
                        records_written=written,
                        partitions_rebuilt=partitions,
                        missing_interval_count=missing,
                        duplicate_records_detected=duplicates,
                    )
                )
            else:
                ranges = (
                    [(to_utc(start), to_utc(end))]
                    if request.refresh
                    else self._store.missing_market_ranges("load", start, end)
                )
                received = written = partitions = duplicates = 0
                for range_start, range_end in ranges:
                    response = await self._market.get_load(
                        LoadRequest(
                            start=range_start,
                            end=range_end,
                            include_forecast=True,
                        ),
                        paginate=False,
                    )
                    rows = [
                        {
                            "interval_start": item.interval_start,
                            "interval_end": item.interval_end,
                            "series": "load",
                            "actual_value": item.load_mw,
                            "forecast_value": item.load_forecast_mw,
                            "unit": "MW",
                            "source_product": response.metadata.source_product
                            or "Actual Forecast Report",
                            "source_retrieved_at": response.metadata.retrieved_at,
                            "observation_type": response.metadata.observation_type.value,
                            "finality": response.metadata.finality.value,
                            "completeness": response.metadata.completeness.value,
                        }
                        for item in response.intervals
                    ]
                    received += len(rows)
                    duplicates += len(rows) - len(
                        {to_utc(item.interval_start) for item in response.intervals}
                    )
                    count, rebuilt = self._store.upsert_market_series(dataset, rows)
                    written += count
                    partitions += rebuilt
                    warnings.extend(response.warnings)
                missing = _missing_count(self._store.missing_market_ranges("load", start, end))
                results.append(
                    HistoricalSyncDatasetResult(
                        dataset=dataset,
                        records_received=received,
                        records_written=written,
                        partitions_rebuilt=partitions,
                        missing_interval_count=missing,
                        duplicate_records_detected=duplicates,
                    )
                )
        return HistoricalStoreSyncResponse(
            results=results,
            metadata=_history_meta(
                "Historical Store Sync",
                start=start,
                end=end,
                count=sum(item.records_written for item in results),
                granularity=request.generation_interval,
                completeness=DataCompleteness.COMPLETE,
                provider=ProviderName.DERIVED,
                observation_type=ObservationType.DERIVED,
                extra={"store_path": str(self._store.root), "schema_version": 1},
            ),
            warnings=list(dict.fromkeys(warnings)),
        )

    async def _sync_generation(
        self,
        request: HistoricalStoreSyncRequest,
        start: datetime,
        end: datetime,
    ) -> HistoricalSyncDatasetResult:
        sources = [
            source
            for source in await self._generation.list_source_files(request.generation_interval)
            if source.intersects(start, end)
        ]
        downloaded = received = written = partitions = duplicates = 0
        for source in sources:
            if not request.refresh and self._store.source_is_current(
                HistoricalDataset.HISTORICAL_GENERATION,
                source.file_id,
                source.source_hash,
            ):
                continue
            source_start = start_of_market_day(source.coverage_start)
            source_end = start_of_market_day(source.coverage_end + timedelta(days=1))
            records, digest = await self._generation.fetch_source_file(
                source,
                interval=request.generation_interval,
                start=source_start,
                end=source_end,
            )
            downloaded += 1
            received += len(records)
            duplicates += len(records) - len(
                {(item.interval_start_utc, item.resolution, item.asset_id) for item in records}
            )
            count, rebuilt = self._store.replace_generation_source(
                records,
                source_file_id=source.file_id,
                source_file_name=source.name,
                source_hash=source.source_hash or digest,
                source_updated_at=source.content_updated_at,
            )
            written += count
            partitions += rebuilt
        missing = self._store.missing_generation_intervals(
            to_utc(start), to_utc(end), request.generation_interval
        )
        return HistoricalSyncDatasetResult(
            dataset=HistoricalDataset.HISTORICAL_GENERATION,
            records_received=received,
            records_written=written,
            source_files_checked=len(sources),
            source_files_downloaded=downloaded,
            partitions_rebuilt=partitions,
            missing_interval_count=missing,
            duplicate_records_detected=duplicates,
        )

    async def get_historical_store_status(self) -> HistoricalStoreStatusResponse:
        if not self._store.dependencies_available():
            return HistoricalStoreStatusResponse(
                store_path=str(self._store.root),
                enabled=False,
                datasets=[],
                metadata=_history_meta(
                    "Historical Store Status",
                    count=0,
                    completeness=DataCompleteness.EMPTY,
                    provider=ProviderName.DERIVED,
                    observation_type=ObservationType.DERIVED,
                ),
                warnings=[
                    "Historical storage is disabled because the analytics extra is not installed."
                ],
            )
        statuses = self._store.statuses()
        return HistoricalStoreStatusResponse(
            store_path=str(self._store.root),
            enabled=True,
            datasets=statuses,
            metadata=_history_meta(
                "Historical Store Status",
                count=sum(item.observation_count for item in statuses),
                completeness=DataCompleteness.COMPLETE,
                provider=ProviderName.DERIVED,
                observation_type=ObservationType.DERIVED,
                extra={"schema_version": 1},
            ),
        )

    async def get_forecast(
        self, request: ForecastRequest, *, paginate: bool = True
    ) -> ForecastResponse:
        start, end = validate_range(
            request.start,
            request.end,
            max_days=self._settings.max_load_days,
            label="forecast range",
        )
        response = await self._market.get_load(
            LoadRequest(start=start, end=end, include_forecast=True), paginate=False
        )
        all_intervals = [
            ForecastInterval(
                interval_start=item.interval_start,
                interval_end=item.interval_end or item.interval_start + timedelta(hours=1),
                series="ail",
                actual_value=item.load_mw,
                forecast_value=item.load_forecast_mw,
                unit="MW",
            )
            for item in response.intervals
        ]
        total = len(all_intervals)
        return ForecastResponse(
            intervals=(
                all_intervals[request.offset : request.offset + request.limit]
                if paginate
                else all_intervals
            ),
            page=_page(
                total,
                request.offset if paginate else 0,
                request.limit if paginate else max(total, 1),
            ),
            metadata=response.metadata.model_copy(
                update={"dataset": "Actual and Forecast Series", "observation_count": total}
            ),
            warnings=response.warnings,
        )

    async def get_uc_settlement_summary(
        self, request: UnitCommitmentSettlementRequest
    ) -> UnitCommitmentSettlementResponse:
        if (request.end_date - request.start_date).days > 366:
            raise InvalidDateRangeError("UC settlement summary range cannot exceed 366 days.")
        (
            intervals,
            publication_time,
            provenance,
        ) = await self._public_reports.get_unit_commitment_settlement_summary(
            request.start_date, request.end_date
        )
        total = len(intervals)
        start = start_of_market_day(request.start_date)
        end = start_of_market_day(request.end_date + timedelta(days=1))
        return UnitCommitmentSettlementResponse(
            intervals=intervals[request.offset : request.offset + request.limit],
            page=_page(total, request.offset, request.limit),
            metadata=_history_meta(
                "Unit Commitment Settlement Summary",
                start=start,
                end=end,
                count=total,
                granularity="1h",
                completeness=DataCompleteness.COMPLETE if total else DataCompleteness.EMPTY,
                provider=ProviderName.AESO_PUBLIC_REPORT,
                publication_time=publication_time,
                extra=provenance,
            ),
        )


def _page(total: int, offset: int, limit: int) -> PageInfo:
    returned = max(0, min(limit, total - offset))
    next_offset = offset + returned if offset + returned < total else None
    return PageInfo(
        offset=offset,
        limit=limit,
        returned=returned,
        total=total,
        next_offset=next_offset,
    )


def _missing_count(ranges: list[tuple[datetime, datetime]]) -> int:
    return sum(int((to_utc(end) - to_utc(start)).total_seconds() / 3600) for start, end in ranges)


def _history_meta(
    dataset: str,
    *,
    count: int,
    completeness: DataCompleteness,
    start: datetime | None = None,
    end: datetime | None = None,
    granularity: str | None = None,
    provider: ProviderName = ProviderName.AESO_CSD_ARCHIVE,
    observation_type: ObservationType = ObservationType.ACTUAL,
    publication_time: datetime | None = None,
    extra: Mapping[str, object] | None = None,
) -> DatasetMetadata:
    now = utc_now()
    extra_values = dict(extra or {})
    source_product_value = extra_values.get("source_product")
    source_product = source_product_value if isinstance(source_product_value, str) else None
    return DatasetMetadata(
        dataset=dataset,
        source_product=(
            "Historical CSD Generation Data"
            if provider == ProviderName.AESO_CSD_ARCHIVE
            else source_product
        ),
        retrieved_at=now,
        served_at=now,
        status=DataStatus.ACTUAL,
        observation_type=observation_type,
        finality=(
            FinalityStatus.UNKNOWN
            if provider == ProviderName.AESO_CSD_ARCHIVE
            else FinalityStatus.FINAL
        ),
        completeness=completeness,
        available_series=[dataset] if count else [],
        units={
            "generation_mw": "MW",
            "total_uc_amount_cad": "CAD",
            "total_charged_volume_mw": "MW",
        },
        observation_granularity=granularity,
        request_start=to_market(start) if start else None,
        request_end=to_market(end) if end else None,
        publication_time=publication_time,
        provider=provider,
        observation_count=count,
        extra=extra_values,
    )
