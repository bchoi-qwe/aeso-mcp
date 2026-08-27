# SPDX-License-Identifier: MIT
"""Typed DuckDB index with partitioned Parquet snapshots for historical research.

The store is intentionally synchronous.  DuckDB, PyArrow, and filesystem work
belong behind the async boundary owned by :mod:`aeso_mcp.services.history`;
keeping this module synchronous makes transaction and replacement semantics
explicit and keeps storage details out of the service and MCP layers.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from aeso_mcp.errors import ConfigurationError, DataValidationError
from aeso_mcp.models.history import (
    HistoricalDataset,
    HistoricalDatasetStatus,
    HistoricalGenerationInterval,
    HistoricalMarketSeries,
    HistoricalMarketSeriesCoverage,
    HistoricalMarketSeriesInterval,
    MarketSeriesSelection,
)
from aeso_mcp.timeutil import MARKET_TZ, to_market

_SCHEMA_VERSION = 2
_MARKET_SERIES_CADENCE = timedelta(hours=1)


class HistoricalStore:
    """Own the internal storage schema; no arbitrary SQL crosses this boundary."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.database_path = root / "history.duckdb"
        self.parquet_root = root / "parquet"

    @staticmethod
    def dependencies_available() -> bool:
        try:
            import duckdb  # noqa: F401
            import pyarrow  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def schema_version(self) -> int:
        """Current on-disk schema version used for new writes."""

        return _SCHEMA_VERSION

    def initialize(self) -> None:
        """Create or migrate the store without deleting existing observations."""

        self.root.mkdir(parents=True, exist_ok=True)
        self.parquet_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.begin()
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS generation (
                        interval_start TIMESTAMPTZ NOT NULL,
                        interval_end TIMESTAMPTZ NOT NULL,
                        interval_start_utc TIMESTAMPTZ NOT NULL,
                        interval_end_utc TIMESTAMPTZ NOT NULL,
                        resolution VARCHAR NOT NULL,
                        asset_id VARCHAR NOT NULL,
                        asset_name VARCHAR,
                        asset_grouping VARCHAR,
                        fuel_type VARCHAR NOT NULL,
                        sub_fuel_type VARCHAR,
                        generation_mw DOUBLE NOT NULL,
                        maximum_capability_mw DOUBLE,
                        system_capability_mw DOUBLE,
                        planning_area VARCHAR,
                        region VARCHAR,
                        source_file_id VARCHAR NOT NULL,
                        source_file_name VARCHAR NOT NULL,
                        source_updated_at TIMESTAMPTZ,
                        source_retrieved_at TIMESTAMPTZ NOT NULL,
                        observation_type VARCHAR NOT NULL,
                        finality VARCHAR NOT NULL,
                        completeness VARCHAR NOT NULL,
                        schema_version INTEGER NOT NULL,
                        PRIMARY KEY (interval_start_utc, resolution, asset_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS market_series (
                        interval_start TIMESTAMPTZ NOT NULL,
                        interval_end TIMESTAMPTZ,
                        interval_start_utc TIMESTAMPTZ,
                        interval_end_utc TIMESTAMPTZ,
                        series VARCHAR NOT NULL,
                        actual_value DOUBLE,
                        forecast_value DOUBLE,
                        unit VARCHAR NOT NULL,
                        source_product VARCHAR NOT NULL,
                        source_retrieved_at TIMESTAMPTZ NOT NULL,
                        source_updated_at TIMESTAMPTZ,
                        source_file_id VARCHAR,
                        source_file_name VARCHAR,
                        source_hash VARCHAR,
                        observation_type VARCHAR NOT NULL,
                        finality VARCHAR NOT NULL,
                        completeness VARCHAR NOT NULL,
                        schema_version INTEGER NOT NULL,
                        PRIMARY KEY (interval_start, series)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS source_manifest (
                        dataset VARCHAR NOT NULL,
                        source_file_id VARCHAR NOT NULL,
                        source_file_name VARCHAR NOT NULL,
                        source_hash VARCHAR NOT NULL,
                        source_updated_at TIMESTAMPTZ,
                        retrieved_at TIMESTAMPTZ NOT NULL,
                        observation_count BIGINT NOT NULL,
                        coverage_start TIMESTAMPTZ,
                        coverage_end TIMESTAMPTZ,
                        observation_type VARCHAR NOT NULL,
                        finality VARCHAR NOT NULL,
                        completeness VARCHAR NOT NULL,
                        schema_version INTEGER NOT NULL,
                        PRIMARY KEY (dataset, source_file_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS store_metadata (
                        key VARCHAR PRIMARY KEY,
                        value VARCHAR NOT NULL
                    )
                    """
                )
                self._migrate_schema(connection)
                connection.execute(
                    "INSERT OR REPLACE INTO store_metadata (key, value) VALUES (?, ?)",
                    ["schema_version", str(_SCHEMA_VERSION)],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def source_is_current(
        self,
        dataset: HistoricalDataset,
        source_file_id: str,
        source_hash: str,
    ) -> bool:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT source_hash FROM source_manifest WHERE dataset = ? AND source_file_id = ?",
                [dataset.value, source_file_id],
            ).fetchone()
        return row is not None and str(row[0]) == source_hash

    def replace_generation_source(
        self,
        records: Sequence[HistoricalGenerationInterval],
        *,
        source_file_id: str,
        source_file_name: str,
        source_hash: str,
        source_updated_at: datetime | None,
    ) -> tuple[int, int]:
        """Atomically replace one published source object and rebuild partitions.

        The database transaction commits before Parquet replacement.  Each
        partition is itself atomically replaced, so an interrupted rebuild
        leaves either the prior complete file or the new complete file; the
        DuckDB index remains the canonical recoverable source.
        """

        self.initialize()
        records_by_key: dict[tuple[datetime, str, str], HistoricalGenerationInterval] = {}
        for record in records:
            key = (record.interval_start_utc, record.resolution, record.asset_id)
            # Keep the final source row for a key.  The archive can contain
            # revised observations in one response; callers report the
            # duplicate count and a later source replacement remains atomic.
            records_by_key[key] = record
        unique_records = list(records_by_key.values())
        rows = [self._plain_row(record.model_dump(mode="python")) for record in unique_records]
        with self._connect() as connection:
            affected = self._existing_generation_partitions(connection, source_file_id)
            affected.update(
                {
                    (
                        record.resolution,
                        record.interval_start_utc.year,
                        record.interval_start_utc.month,
                    )
                    for record in unique_records
                }
            )
            connection.begin()
            try:
                connection.execute(
                    "DELETE FROM generation WHERE source_file_id = ?", [source_file_id]
                )
                if rows:
                    self._insert_arrow_rows(connection, "incoming_generation", rows, "generation")
                connection.execute(
                    """
                    INSERT OR REPLACE INTO source_manifest (
                        dataset, source_file_id, source_file_name, source_hash,
                        source_updated_at, retrieved_at, observation_count,
                        coverage_start, coverage_end, observation_type, finality,
                        completeness, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        HistoricalDataset.HISTORICAL_GENERATION.value,
                        source_file_id,
                        source_file_name,
                        source_hash,
                        source_updated_at,
                        datetime.now(UTC),
                        len(rows),
                        min((record.interval_start_utc for record in unique_records), default=None),
                        max((record.interval_end_utc for record in unique_records), default=None),
                        "actual",
                        "unknown",
                        "complete",
                        _SCHEMA_VERSION,
                    ],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            rebuilt = self._rebuild_generation_partitions(connection, sorted(affected))
        return len(rows), rebuilt

    def upsert_market_series(
        self,
        dataset: HistoricalDataset,
        rows: Sequence[dict[str, Any]],
    ) -> tuple[int, int]:
        """Validate and atomically upsert typed Pool Price or AIL observations."""

        self.initialize()
        if not rows:
            return 0, 0
        expected_series = "pool_price" if dataset == HistoricalDataset.POOL_PRICE else "ail"
        by_key: dict[tuple[datetime, str], dict[str, Any]] = {}
        for row in rows:
            normalized_row = self._normalize_market_row(row)
            if normalized_row["series"] != expected_series:
                raise DataValidationError(
                    f"Dataset {dataset.value!r} cannot contain series {normalized_row['series']!r}."
                )
            key = (normalized_row["interval_start_utc"], normalized_row["series"])
            # A revised publication may repeat an interval with a changed
            # value.  Last-source-row-wins preserves the historical API's
            # deterministic behavior while service-level duplicate counters
            # make the revision observable to callers.
            by_key[key] = normalized_row
        normalized = list(by_key.values())
        partitions = sorted(
            {
                (
                    dataset.value,
                    row["interval_start_utc"].year,
                    row["interval_start_utc"].month,
                )
                for row in normalized
            }
        )
        with self._connect() as connection:
            connection.begin()
            try:
                self._insert_arrow_rows(
                    connection, "incoming_market_series", normalized, "market_series"
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            rebuilt = self._rebuild_market_partitions(connection, partitions)
        return len(normalized), rebuilt

    def missing_market_ranges(
        self,
        series: str | HistoricalMarketSeries,
        start: datetime,
        end: datetime,
        *,
        cadence: timedelta = _MARKET_SERIES_CADENCE,
        value_kind: Literal["actual", "forecast"] = "actual",
    ) -> list[tuple[datetime, datetime]]:
        """Return contiguous absent cadence intervals for incremental fetch/backfill."""

        canonical, forecast_only = _canonical_series(series)
        self.initialize()
        value_column = "forecast_value" if value_kind == "forecast" else "actual_value"
        series_values = [canonical, "load"] if canonical == "ail" else [canonical]
        placeholders = ", ".join("?" for _ in series_values)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT COALESCE(interval_start_utc, interval_start), finality,
                       completeness, {value_column}, forecast_value
                FROM market_series
                WHERE series IN ({placeholders})
                  AND COALESCE(interval_start_utc, interval_start) >= ?
                  AND COALESCE(interval_start_utc, interval_start) < ?
                ORDER BY COALESCE(interval_start_utc, interval_start)
                """,  # noqa: S608
                [*series_values, _as_utc(start), _as_utc(end)],
            ).fetchall()
        observed = {
            _as_utc(row[0])
            for row in rows
            if str(row[1]) != "preliminary"
            and str(row[2]) not in {"partial", "degraded"}
            and row[3] is not None
            and (not forecast_only or row[4] is not None)
        }
        return _missing_ranges(observed, start, end, cadence)

    def query_market_series(
        self,
        series: MarketSeriesSelection | HistoricalMarketSeries | str,
        *,
        start: datetime,
        end: datetime,
        offset: int = 0,
        limit: int = 500,
        paginate: bool = True,
        value_kind: Literal["either", "actual", "forecast"] = "either",
    ) -> tuple[list[HistoricalMarketSeriesInterval], int]:
        """Read a bounded, typed market-series page with bound user values."""

        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit < 1:
            raise ValueError("limit must be positive")
        requested_series = str(getattr(series, "value", series)).strip().lower()
        canonical, forecast_only = _canonical_series(series)
        self.initialize()
        series_values = [canonical, "load"] if canonical == "ail" else [canonical]
        predicates = [
            "COALESCE(interval_start_utc, interval_start) >= ?",
            "COALESCE(interval_start_utc, interval_start) < ?",
            f"series IN ({', '.join('?' for _ in series_values)})",
        ]
        params: list[Any] = [_as_utc(start), _as_utc(end), *series_values]
        if value_kind == "actual":
            predicates.append("actual_value IS NOT NULL")
        elif value_kind == "forecast":
            predicates.append("forecast_value IS NOT NULL")
        if forecast_only:
            predicates.append("forecast_value IS NOT NULL")
        where = " AND ".join(predicates)
        with self._connect() as connection:
            total = int(
                _required_row(
                    connection.execute(
                        f"SELECT count(*) FROM market_series WHERE {where}",  # noqa: S608
                        params,
                    ).fetchone()
                )[0]
            )
            query = (
                "SELECT interval_start, interval_end, series, actual_value, forecast_value, unit, "  # noqa: S608
                "source_product, source_retrieved_at, source_updated_at, source_file_id, "
                "source_file_name, observation_type, finality, completeness, schema_version "
                f"FROM market_series WHERE {where} "
                "ORDER BY COALESCE(interval_start_utc, interval_start), series"
            )
            query_params = list(params)
            if paginate:
                query += " LIMIT ? OFFSET ?"
                query_params.extend([limit, offset])
            cursor = connection.execute(query, query_params)
            rows = cursor.fetchall()
        output_series = requested_series if forecast_only else canonical
        return [_market_row_to_model(row, output_series) for row in rows], total

    def query_market_series_complete(
        self,
        series: MarketSeriesSelection | HistoricalMarketSeries | str,
        *,
        start: datetime,
        end: datetime,
        value_kind: Literal["either", "actual", "forecast"] = "either",
    ) -> tuple[list[HistoricalMarketSeriesInterval], int]:
        """Read the complete matching range for internal analytics."""

        return self.query_market_series(
            series,
            start=start,
            end=end,
            paginate=False,
            value_kind=value_kind,
        )

    def query_pool_price(
        self,
        *,
        start: datetime,
        end: datetime,
        offset: int = 0,
        limit: int = 500,
        paginate: bool = True,
        value_kind: Literal["either", "actual", "forecast"] = "either",
    ) -> tuple[list[HistoricalMarketSeriesInterval], int]:
        """Typed Pool Price actual/forecast query."""

        return self.query_market_series(
            "pool_price",
            start=start,
            end=end,
            offset=offset,
            limit=limit,
            paginate=paginate,
            value_kind=value_kind,
        )

    def query_ail(
        self,
        *,
        start: datetime,
        end: datetime,
        offset: int = 0,
        limit: int = 500,
        paginate: bool = True,
        value_kind: Literal["either", "actual", "forecast"] = "either",
    ) -> tuple[list[HistoricalMarketSeriesInterval], int]:
        """Typed Alberta Internal Load actual/forecast query."""

        return self.query_market_series(
            "ail",
            start=start,
            end=end,
            offset=offset,
            limit=limit,
            paginate=paginate,
            value_kind=value_kind,
        )

    def market_series_coverage(
        self,
        series: MarketSeriesSelection | HistoricalMarketSeries | str,
        *,
        start: datetime,
        end: datetime,
        cadence: timedelta = _MARKET_SERIES_CADENCE,
        value_kind: Literal["actual", "forecast"] = "actual",
    ) -> HistoricalMarketSeriesCoverage:
        """Return DST-safe coverage, finality, completeness, and provenance."""

        canonical, forecast_only = _canonical_series(series)
        self.initialize()
        value_column = "forecast_value" if value_kind == "forecast" else "actual_value"
        series_values = [canonical, "load"] if canonical == "ail" else [canonical]
        placeholders = ", ".join("?" for _ in series_values)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT COALESCE(interval_start_utc, interval_start), {value_column},
                       forecast_value, finality, completeness, source_product
                FROM market_series
                WHERE series IN ({placeholders})
                  AND COALESCE(interval_start_utc, interval_start) >= ?
                  AND COALESCE(interval_start_utc, interval_start) < ?
                """,  # noqa: S608
                [*series_values, _as_utc(start), _as_utc(end)],
            ).fetchall()
        observed_instants = {
            _as_utc(row[0])
            for row in rows
            if row[1] is not None and (not forecast_only or row[2] is not None)
        }
        expected = max(0, int((_as_utc(end) - _as_utc(start)) / cadence))
        observed = len(observed_instants)
        missing = max(expected - observed, 0)
        finalities = {str(row[3]) for row in rows if row[1] is not None}
        completeness_values = {str(row[4]) for row in rows if row[1] is not None}
        finality = _aggregate_finality(finalities)
        completeness = _aggregate_completeness(
            observed=observed,
            expected=expected,
            values=completeness_values,
            finalities=finalities,
        )
        products = sorted({str(row[5]) for row in rows if row[5]})
        return HistoricalMarketSeriesCoverage(
            series=cast(
                MarketSeriesSelection,
                f"{canonical}_forecast" if forecast_only else canonical,
            ),
            start=to_market(start),
            end=to_market(end),
            cadence=_cadence_name(cadence),
            expected_observations=expected,
            observed_observations=observed,
            missing_observations=missing,
            complete=(missing == 0 and finality != "preliminary" and completeness == "complete"),
            finality=finality,
            completeness=completeness,
            source_products=products,
            schema_version=_SCHEMA_VERSION,
        )

    def is_market_series_complete(
        self,
        series: MarketSeriesSelection | HistoricalMarketSeries | str,
        *,
        start: datetime,
        end: datetime,
        cadence: timedelta = _MARKET_SERIES_CADENCE,
        value_kind: Literal["actual", "forecast"] = "actual",
    ) -> bool:
        """Return whether a stored series safely covers every requested interval."""

        return self.market_series_coverage(
            series,
            start=start,
            end=end,
            cadence=cadence,
            value_kind=value_kind,
        ).complete

    def query_generation(
        self,
        *,
        start: datetime,
        end: datetime,
        resolution: str,
        asset_ids: Sequence[str],
        fuel_types: Sequence[str],
        offset: int,
        limit: int,
    ) -> tuple[list[HistoricalGenerationInterval], int]:
        self.initialize()
        predicates = ["interval_start_utc >= ?", "interval_start_utc < ?", "resolution = ?"]
        params: list[Any] = [_as_utc(start), _as_utc(end), resolution]
        if asset_ids:
            placeholders = ", ".join("?" for _ in asset_ids)
            predicates.append(f"upper(asset_id) IN ({placeholders})")
            params.extend(asset_ids)
        if fuel_types:
            placeholders = ", ".join("?" for _ in fuel_types)
            predicates.append(f"upper(fuel_type) IN ({placeholders})")
            params.extend(fuel_types)
        where = " AND ".join(predicates)
        with self._connect() as connection:
            total = int(
                _required_row(
                    connection.execute(
                        f"SELECT count(*) FROM generation WHERE {where}",  # noqa: S608
                        params,
                    ).fetchone()
                )[0]
            )
            cursor = connection.execute(
                f"SELECT * FROM generation WHERE {where} "  # noqa: S608
                "ORDER BY interval_start_utc, asset_id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )
            columns = [item[0] for item in cursor.description]
            records = [
                HistoricalGenerationInterval.model_validate(dict(zip(columns, row, strict=True)))
                for row in cursor.fetchall()
            ]
        return records, total

    def statuses(self) -> list[HistoricalDatasetStatus]:
        self.initialize()
        statuses: list[HistoricalDatasetStatus] = []
        with self._connect() as connection:
            generation = _required_row(
                connection.execute(
                    """
                    SELECT count(*), min(interval_start_utc), max(interval_start_utc),
                           count(DISTINCT source_file_id) FROM generation
                    """
                ).fetchone()
            )
            statuses.append(
                HistoricalDatasetStatus(
                    dataset=HistoricalDataset.HISTORICAL_GENERATION,
                    observation_count=int(generation[0]),
                    earliest_interval=generation[1],
                    latest_interval=generation[2],
                    source_file_count=int(generation[3]),
                    parquet_partition_count=self._partition_count("historical_generation"),
                    detected_gap_count=self._generation_gap_count(connection),
                    schema_version=_SCHEMA_VERSION,
                )
            )
            for dataset, series in (
                (HistoricalDataset.POOL_PRICE, "pool_price"),
                (HistoricalDataset.LOAD, "ail"),
            ):
                series_values = [series, "load"] if series == "ail" else [series]
                placeholders = ", ".join("?" for _ in series_values)
                row = _required_row(
                    connection.execute(
                        f"""
                        SELECT count(*),
                               min(COALESCE(interval_start_utc, interval_start)),
                               max(COALESCE(interval_start_utc, interval_start)),
                               count(DISTINCT source_file_id)
                        FROM market_series WHERE series IN ({placeholders})
                        """,  # noqa: S608
                        series_values,
                    ).fetchone()
                )
                statuses.append(
                    HistoricalDatasetStatus(
                        dataset=dataset,
                        observation_count=int(row[0]),
                        earliest_interval=row[1],
                        latest_interval=row[2],
                        source_file_count=int(row[3]),
                        parquet_partition_count=self._partition_count(dataset.value),
                        detected_gap_count=self._market_gap_count(
                            connection, series, row[1], row[2]
                        ),
                        schema_version=_SCHEMA_VERSION,
                    )
                )
        return statuses

    def missing_generation_intervals(self, start: datetime, end: datetime, resolution: str) -> int:
        """Count missing distinct timestamps; asset-level gaps remain source-specific."""

        cadence = timedelta(hours=1) if resolution == "hourly" else timedelta(minutes=5)
        expected = max(0, int((_as_utc(end) - _as_utc(start)) / cadence))
        self.initialize()
        with self._connect() as connection:
            observed = int(
                _required_row(
                    connection.execute(
                        """
                        SELECT count(DISTINCT interval_start_utc) FROM generation
                        WHERE interval_start_utc >= ? AND interval_start_utc < ? AND resolution = ?
                        """,
                        [_as_utc(start), _as_utc(end), resolution],
                    ).fetchone()
                )[0]
            )
        return max(expected - observed, 0)

    def _connect(self):
        try:
            import duckdb
        except ImportError as exc:
            raise ConfigurationError(
                "Historical storage requires the optional analytics dependencies; install "
                "with `pip install 'aeso-mcp[analytics]'`."
            ) from exc
        return duckdb.connect(str(self.database_path))

    @staticmethod
    def _migrate_schema(connection: Any) -> None:
        """Upgrade unreleased local schemas without discarding user data."""

        existing_version_row = connection.execute(
            "SELECT value FROM store_metadata WHERE key = ?", ["schema_version"]
        ).fetchone()
        if existing_version_row is not None:
            try:
                existing_version = int(existing_version_row[0])
            except (TypeError, ValueError) as exc:
                raise ConfigurationError("Historical store schema version is invalid.") from exc
            if existing_version > _SCHEMA_VERSION:
                raise ConfigurationError(
                    f"Historical store schema {existing_version} is newer than supported "
                    f"schema {_SCHEMA_VERSION}."
                )

        additions: dict[str, dict[str, str]] = {
            "generation": {
                "schema_version": "INTEGER DEFAULT 1",
                "source_retrieved_at": "TIMESTAMPTZ DEFAULT current_timestamp",
                "observation_type": "VARCHAR DEFAULT 'actual'",
                "finality": "VARCHAR DEFAULT 'unknown'",
                "completeness": "VARCHAR DEFAULT 'complete'",
            },
            "market_series": {
                "interval_start_utc": "TIMESTAMPTZ",
                "interval_end_utc": "TIMESTAMPTZ",
                "source_retrieved_at": "TIMESTAMPTZ DEFAULT current_timestamp",
                "source_updated_at": "TIMESTAMPTZ",
                "source_file_id": "VARCHAR",
                "source_file_name": "VARCHAR",
                "source_hash": "VARCHAR",
                "observation_type": "VARCHAR DEFAULT 'actual'",
                "finality": "VARCHAR DEFAULT 'unknown'",
                "completeness": "VARCHAR DEFAULT 'complete'",
                "schema_version": "INTEGER DEFAULT 1",
            },
            "source_manifest": {
                "coverage_start": "TIMESTAMPTZ",
                "coverage_end": "TIMESTAMPTZ",
                "observation_type": "VARCHAR DEFAULT 'actual'",
                "finality": "VARCHAR DEFAULT 'unknown'",
                "completeness": "VARCHAR DEFAULT 'complete'",
                "schema_version": "INTEGER DEFAULT 1",
            },
        }
        for table, columns in additions.items():
            existing = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()
            }
            for column, definition in columns.items():
                if column not in existing:
                    connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')

        # Old stores keyed market rows by ``interval_start``.  DuckDB's
        # TIMESTAMPTZ comparison is instant-based, so filling UTC shadow columns
        # preserves all observations while making DST-safe reads explicit.
        connection.execute(
            "UPDATE market_series SET interval_start_utc = interval_start "
            "WHERE interval_start_utc IS NULL"
        )
        connection.execute(
            "UPDATE market_series SET interval_end_utc = interval_end "
            "WHERE interval_end_utc IS NULL AND interval_end IS NOT NULL"
        )
        for table in ("generation", "market_series", "source_manifest"):
            connection.execute(
                f"UPDATE {table} SET schema_version = ? WHERE schema_version IS NULL "  # noqa: S608
                "OR schema_version < ?",
                [_SCHEMA_VERSION, _SCHEMA_VERSION],
            )

    @staticmethod
    def _arrow_table(rows: Sequence[dict[str, Any]]):
        try:
            import pyarrow as pa
        except ImportError as exc:
            raise ConfigurationError(
                "Historical storage requires the optional analytics dependencies; install "
                "with `pip install 'aeso-mcp[analytics]'`."
            ) from exc
        return pa.Table.from_pylist(list(rows))

    def _insert_arrow_rows(
        self,
        connection: Any,
        registration: str,
        rows: Sequence[dict[str, Any]],
        table_name: str,
    ) -> None:
        table = self._arrow_table(rows)
        connection.register(registration, table)
        try:
            connection.execute(
                f"INSERT OR REPLACE INTO {table_name} BY NAME SELECT * FROM {registration}"  # noqa: S608
            )
        finally:
            connection.unregister(registration)

    def _rebuild_generation_partitions(
        self, connection: Any, partitions: Iterable[tuple[str, int, int]]
    ) -> int:
        rebuilt = 0
        for resolution, year, month in partitions:
            target = self._partition_path("historical_generation", year, month, resolution)
            self._copy_partition(
                connection,
                target,
                """
                SELECT * FROM generation
                WHERE resolution = ?
                  AND year(timezone('UTC', interval_start_utc)) = ?
                  AND month(timezone('UTC', interval_start_utc)) = ?
                ORDER BY interval_start_utc, asset_id
                """,
                [resolution, year, month],
            )
            rebuilt += 1
        return rebuilt

    def _rebuild_market_partitions(
        self, connection: Any, partitions: Iterable[tuple[str, int, int]]
    ) -> int:
        rebuilt = 0
        for dataset, year, month in partitions:
            series = "pool_price" if dataset == HistoricalDataset.POOL_PRICE.value else "ail"
            target = self._partition_path(dataset, year, month)
            self._copy_partition(
                connection,
                target,
                """
                SELECT * FROM market_series
                WHERE series IN (?, ?)
                  AND year(timezone('UTC', COALESCE(interval_start_utc, interval_start))) = ?
                  AND month(timezone('UTC', COALESCE(interval_start_utc, interval_start))) = ?
                ORDER BY COALESCE(interval_start_utc, interval_start), series
                """,
                [series, "load" if series == "ail" else series, year, month],
            )
            rebuilt += 1
        return rebuilt

    def _partition_path(
        self, dataset: str, year: int, month: int, resolution: str | None = None
    ) -> Path:
        root = self.parquet_root / f"dataset={dataset}"
        if resolution is not None:
            root /= f"resolution={resolution}"
        return root / f"year={year:04d}" / f"month={month:02d}" / "data.parquet"

    @staticmethod
    def _copy_partition(
        connection: Any,
        target: Path,
        select_sql: str,
        params: Sequence[Any],
    ) -> None:
        """Write a Parquet partition through a same-directory atomic replace."""

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        escaped = str(temporary).replace("'", "''")
        try:
            connection.execute(
                f"COPY ({select_sql}) TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)",
                params,
            )
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _partition_count(self, dataset: str) -> int:
        root = self.parquet_root / f"dataset={dataset}"
        return sum(1 for _ in root.rglob("data.parquet")) if root.exists() else 0

    @staticmethod
    def _generation_gap_count(connection: Any) -> int:
        gaps = 0
        for resolution, seconds in (("hourly", 3600), ("5-minute", 300)):
            row = _required_row(
                connection.execute(
                    """
                    SELECT min(interval_start_utc), max(interval_start_utc),
                           count(DISTINCT interval_start_utc)
                    FROM generation WHERE resolution = ?
                    """,
                    [resolution],
                ).fetchone()
            )
            if row[0] is None or row[1] is None:
                continue
            expected = int((_as_utc(row[1]) - _as_utc(row[0])).total_seconds() / seconds) + 1
            gaps += max(expected - int(row[2]), 0)
        return gaps

    @staticmethod
    def _market_gap_count(
        connection: Any, series: str, earliest: datetime | None, latest: datetime | None
    ) -> int:
        if earliest is None or latest is None:
            return 0
        series_values = [series, "load"] if series == "ail" else [series]
        placeholders = ", ".join("?" for _ in series_values)
        observed = int(
            _required_row(
                connection.execute(
                    f"""
                    SELECT count(DISTINCT COALESCE(interval_start_utc, interval_start))
                    FROM market_series WHERE series IN ({placeholders})
                    """,  # noqa: S608
                    series_values,
                ).fetchone()
            )[0]
        )
        expected = int((_as_utc(latest) - _as_utc(earliest)).total_seconds() / 3600) + 1
        return max(expected - observed, 0)

    @staticmethod
    def _existing_generation_partitions(
        connection: Any, source_file_id: str
    ) -> set[tuple[str, int, int]]:
        rows = connection.execute(
            """
            SELECT DISTINCT resolution,
                            year(timezone('UTC', interval_start_utc)),
                            month(timezone('UTC', interval_start_utc))
            FROM generation WHERE source_file_id = ?
            """,
            [source_file_id],
        ).fetchall()
        return {(str(row[0]), int(row[1]), int(row[2])) for row in rows}

    @classmethod
    def _normalize_market_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        start_value = row.get("interval_start")
        if not isinstance(start_value, datetime):
            raise DataValidationError("Market-series interval_start must be a datetime.")
        start_utc = _as_utc(start_value)
        end_value = row.get("interval_end")
        end_utc = (
            _as_utc(end_value)
            if isinstance(end_value, datetime)
            else start_utc + timedelta(hours=1)
        )
        if end_utc <= start_utc:
            raise DataValidationError("Market-series interval_end must be after interval_start.")
        actual = row.get("actual_value")
        forecast = row.get("forecast_value")
        if actual is None and forecast is None:
            raise DataValidationError(
                "Market-series rows must contain an actual_value or forecast_value."
            )
        canonical, _forecast_only = _canonical_series(str(row.get("series", "")))
        source_product = row.get("source_product")
        if not isinstance(source_product, str) or not source_product.strip():
            raise DataValidationError("Market-series source_product is required.")
        retrieved = row.get("source_retrieved_at")
        if not isinstance(retrieved, datetime):
            retrieved = datetime.now(UTC)
        return {
            "interval_start": start_utc,
            "interval_end": end_utc,
            "interval_start_utc": start_utc,
            "interval_end_utc": end_utc,
            "series": canonical,
            "actual_value": float(actual) if actual is not None else None,
            "forecast_value": float(forecast) if forecast is not None else None,
            "unit": str(row.get("unit") or "unknown"),
            "source_product": source_product.strip(),
            "source_retrieved_at": _as_utc(retrieved),
            "source_updated_at": (
                _as_utc(row["source_updated_at"])
                if isinstance(row.get("source_updated_at"), datetime)
                else None
            ),
            "source_file_id": _optional_text(row.get("source_file_id")),
            "source_file_name": _optional_text(row.get("source_file_name")),
            "source_hash": _optional_text(row.get("source_hash")),
            "observation_type": _enum_text(row.get("observation_type"), "actual"),
            "finality": _enum_text(row.get("finality"), "unknown"),
            "completeness": _enum_text(row.get("completeness"), "complete"),
            "schema_version": _SCHEMA_VERSION,
        }

    @staticmethod
    def _plain_row(row: dict[str, Any]) -> dict[str, Any]:
        plain = {key: _enum_text(value, value) for key, value in row.items()}
        if "schema_version" in plain:
            plain["schema_version"] = _SCHEMA_VERSION
        return plain


def _canonical_series(
    series: str | HistoricalMarketSeries | MarketSeriesSelection,
) -> tuple[str, bool]:
    value = getattr(series, "value", series)
    text = str(value).strip().lower()
    aliases = {
        "pool_price": ("pool_price", False),
        "pool_price_forecast": ("pool_price", True),
        "ail": ("ail", False),
        "ail_forecast": ("ail", True),
        "load": ("ail", False),
        "load_forecast": ("ail", True),
    }
    try:
        return aliases[text]
    except KeyError as exc:
        raise ValueError(
            "Unsupported historical market series; expected pool_price, pool_price_forecast, "
            "ail, or ail_forecast."
        ) from exc


def _missing_ranges(
    observed: set[datetime],
    start: datetime,
    end: datetime,
    cadence: timedelta,
) -> list[tuple[datetime, datetime]]:
    missing: list[datetime] = []
    current = _as_utc(start)
    end_utc = _as_utc(end)
    while current < end_utc:
        if current not in observed:
            missing.append(current)
        current += cadence
    if not missing:
        return []
    ranges: list[tuple[datetime, datetime]] = []
    range_start = previous = missing[0]
    for value in missing[1:]:
        if value != previous + cadence:
            ranges.append((range_start, previous + cadence))
            range_start = value
        previous = value
    ranges.append((range_start, previous + cadence))
    return ranges


def _market_row_to_model(row: tuple[Any, ...], series: str) -> HistoricalMarketSeriesInterval:
    (
        interval_start,
        interval_end,
        _stored_series,
        actual,
        forecast,
        unit,
        source_product,
        source_retrieved_at,
        source_updated_at,
        source_file_id,
        source_file_name,
        observation_type,
        finality,
        completeness,
        schema_version,
    ) = row
    start_utc = _as_utc(interval_start)
    end_utc = _as_utc(interval_end) if interval_end is not None else start_utc + timedelta(hours=1)
    return HistoricalMarketSeriesInterval(
        interval_start=start_utc.astimezone(MARKET_TZ),
        interval_end=end_utc.astimezone(MARKET_TZ),
        series=series,  # type: ignore[arg-type]
        actual_value=float(actual) if actual is not None else None,
        forecast_value=float(forecast) if forecast is not None else None,
        unit=str(unit),
        source_product=str(source_product),
        source_retrieved_at=_as_utc(source_retrieved_at),
        source_updated_at=(_as_utc(source_updated_at) if source_updated_at is not None else None),
        source_file_id=str(source_file_id) if source_file_id is not None else None,
        source_file_name=str(source_file_name) if source_file_name is not None else None,
        observation_type=observation_type,
        finality=finality,
        completeness=completeness,
        schema_version=int(schema_version),
    )


def _aggregate_finality(values: set[str]):
    from aeso_mcp.models.common import FinalityStatus

    if "preliminary" in values:
        return FinalityStatus.PRELIMINARY
    if values and values == {"final"}:
        return FinalityStatus.FINAL
    return FinalityStatus.UNKNOWN


def _aggregate_completeness(
    *, observed: int, expected: int, values: set[str], finalities: set[str]
):
    from aeso_mcp.models.common import DataCompleteness

    if not observed:
        return DataCompleteness.EMPTY
    if "degraded" in values:
        return DataCompleteness.DEGRADED
    if "partial" in values or observed < expected or "preliminary" in finalities:
        return DataCompleteness.PARTIAL
    return DataCompleteness.COMPLETE


def _cadence_name(cadence: timedelta) -> str:
    seconds = int(cadence.total_seconds())
    if seconds == 3600:
        return "1h"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def _enum_text(value: Any, default: Any) -> Any:
    if value is None:
        value = default
    return getattr(value, "value", value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _required_row(row: tuple[Any, ...] | None) -> tuple[Any, ...]:
    if row is None:
        raise RuntimeError("DuckDB aggregate query returned no row.")
    return row
