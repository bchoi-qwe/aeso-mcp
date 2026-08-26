# SPDX-License-Identifier: MIT
"""Typed DuckDB index with partitioned Parquet snapshots for historical research."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aeso_mcp.errors import ConfigurationError
from aeso_mcp.models.history import (
    HistoricalDataset,
    HistoricalDatasetStatus,
    HistoricalGenerationInterval,
)

_SCHEMA_VERSION = 1


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

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.parquet_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
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
                    series VARCHAR NOT NULL,
                    actual_value DOUBLE,
                    forecast_value DOUBLE,
                    unit VARCHAR NOT NULL,
                    source_product VARCHAR NOT NULL,
                    source_retrieved_at TIMESTAMPTZ NOT NULL,
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
            self._migrate_schema(connection)

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
        """Atomically replace one published source object and rebuild affected partitions."""
        self.initialize()
        records_by_key = {
            (record.interval_start_utc, record.resolution, record.asset_id): record
            for record in records
        }
        unique_records = list(records_by_key.values())
        rows = [record.model_dump(mode="python") for record in unique_records]
        affected = sorted(
            {
                (record.resolution, record.interval_start_utc.year, record.interval_start_utc.month)
                for record in unique_records
            }
        )
        with self._connect() as connection:
            connection.begin()
            try:
                connection.execute(
                    "DELETE FROM generation WHERE source_file_id = ?", [source_file_id]
                )
                if rows:
                    table = self._arrow_table(rows)
                    connection.register("incoming_generation", table)
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO generation BY NAME
                        SELECT * FROM incoming_generation
                        """
                    )
                    connection.unregister("incoming_generation")
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
            rebuilt = self._rebuild_generation_partitions(connection, affected)
        return len(rows), rebuilt

    def upsert_market_series(
        self,
        dataset: HistoricalDataset,
        rows: Sequence[dict[str, Any]],
    ) -> tuple[int, int]:
        self.initialize()
        if not rows:
            return 0, 0
        rows_by_key = {(_as_utc(row["interval_start"]), str(row["series"])): row for row in rows}
        normalized = [dict(row, schema_version=_SCHEMA_VERSION) for row in rows_by_key.values()]
        partitions = sorted(
            {
                (
                    dataset.value,
                    _as_utc(row["interval_start"]).year,
                    _as_utc(row["interval_start"]).month,
                )
                for row in normalized
            }
        )
        with self._connect() as connection:
            table = self._arrow_table(normalized)
            connection.register("incoming_market_series", table)
            connection.execute(
                "INSERT OR REPLACE INTO market_series BY NAME SELECT * FROM incoming_market_series"
            )
            connection.unregister("incoming_market_series")
            rebuilt = self._rebuild_market_partitions(connection, partitions)
        return len(normalized), rebuilt

    def missing_market_ranges(
        self,
        series: str,
        start: datetime,
        end: datetime,
        *,
        cadence: timedelta = timedelta(hours=1),
    ) -> list[tuple[datetime, datetime]]:
        """Return contiguous absent cadence intervals for incremental fetch/backfill."""
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT interval_start, finality FROM market_series
                WHERE series = ? AND interval_start >= ? AND interval_start < ?
                ORDER BY interval_start
                """,
                [series, start, end],
            ).fetchall()
        observed = {_as_utc(row[0]) for row in rows if str(row[1]) != "preliminary"}
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
        params: list[Any] = [start, end, resolution]
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
            # Every predicate fragment is selected above; user values remain bound parameters.
            total = int(
                _required_row(
                    connection.execute(
                        f"SELECT count(*) FROM generation WHERE {where}",  # noqa: S608
                        params,
                    ).fetchone()
                )[0]
            )
            query = (
                f"SELECT * FROM generation WHERE {where} "  # noqa: S608
                "ORDER BY interval_start_utc, asset_id LIMIT ? OFFSET ?"
            )
            cursor = connection.execute(
                query,
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
                )
            )
            for dataset, series in (
                (HistoricalDataset.POOL_PRICE, "pool_price"),
                (HistoricalDataset.LOAD, "load"),
            ):
                row = _required_row(
                    connection.execute(
                        "SELECT count(*), min(interval_start), max(interval_start) "
                        "FROM market_series WHERE series = ?",
                        [series],
                    ).fetchone()
                )
                statuses.append(
                    HistoricalDatasetStatus(
                        dataset=dataset,
                        observation_count=int(row[0]),
                        earliest_interval=row[1],
                        latest_interval=row[2],
                        parquet_partition_count=self._partition_count(dataset.value),
                        detected_gap_count=self._market_gap_count(
                            connection, series, row[1], row[2]
                        ),
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
                        [start, end, resolution],
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
        additions = {
            "generation": {
                "source_retrieved_at": "TIMESTAMPTZ DEFAULT current_timestamp",
                "observation_type": "VARCHAR DEFAULT 'actual'",
                "finality": "VARCHAR DEFAULT 'unknown'",
                "completeness": "VARCHAR DEFAULT 'complete'",
            },
            "market_series": {
                "source_retrieved_at": "TIMESTAMPTZ DEFAULT current_timestamp",
                "observation_type": "VARCHAR DEFAULT 'actual'",
                "finality": "VARCHAR DEFAULT 'unknown'",
                "completeness": "VARCHAR DEFAULT 'complete'",
            },
            "source_manifest": {
                "coverage_start": "TIMESTAMPTZ",
                "coverage_end": "TIMESTAMPTZ",
                "observation_type": "VARCHAR DEFAULT 'actual'",
                "finality": "VARCHAR DEFAULT 'unknown'",
                "completeness": "VARCHAR DEFAULT 'complete'",
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
                WHERE resolution = ? AND year(interval_start_utc) = ? AND month(interval_start_utc) = ?
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
            series = "pool_price" if dataset == HistoricalDataset.POOL_PRICE.value else "load"
            target = self._partition_path(dataset, year, month)
            self._copy_partition(
                connection,
                target,
                """
                SELECT * FROM market_series
                WHERE series = ? AND year(interval_start) = ? AND month(interval_start) = ?
                ORDER BY interval_start
                """,
                [series, year, month],
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
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        escaped = str(target).replace("'", "''")
        connection.execute(
            f"COPY ({select_sql}) TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)",
            params,
        )

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
        observed = int(
            _required_row(
                connection.execute(
                    "SELECT count(DISTINCT interval_start) FROM market_series WHERE series = ?",
                    [series],
                ).fetchone()
            )[0]
        )
        expected = int((_as_utc(latest) - _as_utc(earliest)).total_seconds() / 3600) + 1
        return max(expected - observed, 0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _required_row(row: tuple[Any, ...] | None) -> tuple[Any, ...]:
    if row is None:
        raise RuntimeError("DuckDB aggregate query returned no row.")
    return row
