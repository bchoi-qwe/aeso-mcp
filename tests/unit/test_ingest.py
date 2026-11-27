# SPDX-License-Identifier: MIT
"""Ingestion CLI validates the information-time interval before doing I/O."""

import pytest

from aeso_mcp.ingest import parse_request
from aeso_mcp.models.history import HistoricalDataset


def test_ingest_requires_explicit_dataset_and_aware_times() -> None:
    request = parse_request(
        [
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-02T00:00:00Z",
            "--dataset",
            "pool_price",
            "--dataset",
            "load",
            "--refresh",
        ]
    )
    assert request.datasets == [HistoricalDataset.POOL_PRICE, HistoricalDataset.LOAD]
    assert request.refresh
    assert request.start.utcoffset() is not None


@pytest.mark.parametrize("start", ["2026-01-01", "2026-01-03T00:00:00Z"])
def test_ingest_rejects_ambiguous_or_reversed_interval(start: str) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_request(["--start", start, "--end", "2026-01-02T00:00:00Z", "--dataset", "load"])
    assert exc.value.code == 2
