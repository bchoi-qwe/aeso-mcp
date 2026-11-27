# SPDX-License-Identifier: MIT
"""Strict observed-actual replay with no look-ahead or missing-to-zero substitution."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aeso_mcp.errors import DataValidationError
from aeso_mcp.models.history import HistoricalDataset
from aeso_mcp.storage.history import HistoricalStore


def test_actual_replay_is_revision_safe_and_outer_aligned(tmp_path: Path) -> None:
    store = HistoricalStore(tmp_path)
    start = datetime(2025, 11, 2, 7, tzinfo=UTC)
    end = start + timedelta(hours=2)
    early = end + timedelta(days=1)
    late = early + timedelta(days=1)
    base = {
        "interval_start": start,
        "interval_end": start + timedelta(hours=1),
        "series": "pool_price",
        "actual_value": 0.0,
        "forecast_value": 99.0,
        "source_product": "Pool Price Report",
        "source_retrieved_at": early,
        "unit": "CAD/MWh",
        "finality": "preliminary",
    }
    store.upsert_market_series(HistoricalDataset.POOL_PRICE, [base])
    store.upsert_market_series(
        HistoricalDataset.POOL_PRICE,
        [{**base, "actual_value": 45.0, "source_retrieved_at": late, "finality": "final"}],
    )
    store.upsert_market_series(
        HistoricalDataset.LOAD,
        [
            {
                **base,
                "interval_start": start + timedelta(hours=1),
                "interval_end": end,
                "series": "ail",
                "actual_value": 9000.0,
                "unit": "MW",
                "source_product": "Alberta Internal Load",
            }
        ],
    )
    before = store.replay_market_actuals(start=start, end=end, as_of=end)
    assert before.missing_pool_price == before.missing_ail == 2
    original = store.replay_market_actuals(start=start, end=end, as_of=early)
    assert original.rows[0].pool_price is not None
    assert original.rows[0].pool_price.actual_value == 0
    assert original.rows[0].pool_price.forecast_value is None
    assert original.rows[0].pool_price.finality == "preliminary"
    assert original.rows[0].ail is None
    assert original.rows[1].pool_price is None
    assert original.rows[1].ail is not None
    assert original.rows[1].ail.actual_value == 9000
    assert original.missing_pool_price == original.missing_ail == 1
    corrected = store.replay_market_actuals(start=start, end=end, as_of=late)
    assert corrected.rows[0].pool_price is not None
    assert corrected.rows[0].pool_price.actual_value == 45
    assert corrected.rows[0].pool_price.finality == "final"
    assert store.replay_market_actuals(start=start, end=end, as_of=early) == original


def test_actual_replay_does_not_seed_unknown_retrievals(tmp_path: Path) -> None:
    store = HistoricalStore(tmp_path)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    store.upsert_market_series(
        HistoricalDataset.POOL_PRICE,
        [
            {
                "interval_start": start,
                "series": "pool_price",
                "actual_value": 20,
                "source_product": "Pool Price Report",
                "unit": "CAD/MWh",
            }
        ],
    )
    result = store.replay_market_actuals(
        start=start, end=end, as_of=datetime(2030, 1, 1, tzinfo=UTC)
    )
    assert result.rows[0].pool_price is None
    with pytest.raises(DataValidationError, match="after as_of"):
        store.replay_market_actuals(start=start, end=end, as_of=start)
