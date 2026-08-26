# SPDX-License-Identifier: MIT
"""DuckDB/Parquet historical-store behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aeso_mcp.models.history import HistoricalDataset, HistoricalGenerationInterval
from aeso_mcp.storage.history import HistoricalStore


def test_generation_source_replace_is_idempotent_and_builds_partition(tmp_path) -> None:
    store = HistoricalStore(tmp_path / "history")
    if not store.dependencies_available():
        pytest.skip("analytics extra is not installed")
    start = datetime(2026, 7, 1, tzinfo=UTC)
    record = HistoricalGenerationInterval(
        interval_start=start,
        interval_end=start + timedelta(hours=1),
        interval_start_utc=start,
        interval_end_utc=start + timedelta(hours=1),
        resolution="hourly",
        asset_id="TEST1",
        asset_name="Test Asset",
        fuel_type="WIND",
        generation_mw=10,
        maximum_capability_mw=20,
        source_file_id="123",
        source_file_name="CSD Generation (Hourly) - 2026-07.zip",
        source_retrieved_at=start,
    )

    written, partitions = store.replace_generation_source(
        [record, record.model_copy(update={"generation_mw": 10.5})],
        source_file_id="123",
        source_file_name=record.source_file_name,
        source_hash="abc123",
        source_updated_at=None,
    )
    assert (written, partitions) == (1, 1)
    assert store.source_is_current(HistoricalDataset.HISTORICAL_GENERATION, "123", "abc123")

    store.replace_generation_source(
        [record.model_copy(update={"generation_mw": 11})],
        source_file_id="123",
        source_file_name=record.source_file_name,
        source_hash="def456",
        source_updated_at=None,
    )
    rows, total = store.query_generation(
        start=start,
        end=start + timedelta(hours=1),
        resolution="hourly",
        asset_ids=["TEST1"],
        fuel_types=[],
        offset=0,
        limit=10,
    )
    assert total == 1
    assert rows[0].generation_mw == 11
    assert len(list(store.parquet_root.rglob("data.parquet"))) == 1


def test_preliminary_market_intervals_remain_refreshable_until_final(tmp_path) -> None:
    store = HistoricalStore(tmp_path / "history")
    if not store.dependencies_available():
        pytest.skip("analytics extra is not installed")
    start = datetime(2026, 7, 1, tzinfo=UTC)
    base_row = {
        "interval_start": start,
        "interval_end": start + timedelta(hours=1),
        "series": "pool_price",
        "actual_value": 42.0,
        "forecast_value": None,
        "unit": "CAD/MWh",
        "source_product": "Pool Price Report",
        "source_retrieved_at": start + timedelta(hours=2),
        "observation_type": "actual",
        "finality": "preliminary",
        "completeness": "complete",
    }

    written, _ = store.upsert_market_series(
        HistoricalDataset.POOL_PRICE,
        [base_row, {**base_row, "actual_value": 42.5}],
    )
    assert written == 1
    assert store.missing_market_ranges("pool_price", start, start + timedelta(hours=1)) == [
        (start, start + timedelta(hours=1))
    ]

    store.upsert_market_series(
        HistoricalDataset.POOL_PRICE,
        [{**base_row, "finality": "final", "actual_value": 43.0}],
    )
    assert store.missing_market_ranges("pool_price", start, start + timedelta(hours=1)) == []
