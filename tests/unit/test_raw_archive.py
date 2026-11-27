# SPDX-License-Identifier: MIT
"""Content identity, chronology and integrity of archived source objects."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aeso_mcp.errors import DataValidationError
from aeso_mcp.storage.raw_archive import RawSourceArchive


def test_archive_deduplicates_bytes_but_retains_observations(tmp_path: Path) -> None:
    archive = RawSourceArchive(tmp_path)
    now = datetime(2026, 11, 27, tzinfo=UTC)
    first = archive.record(
        b"original", dataset="csd", source_id="1", source_name="file.zip", retrieved_at=now
    )
    repeated = archive.record(
        b"original",
        dataset="csd",
        source_id="1",
        source_name="file.zip",
        retrieved_at=now + timedelta(hours=1),
    )
    revised = archive.record(
        b"revised", dataset="csd", source_id="1", source_name="file.zip", retrieved_at=now
    )
    assert first.sha256 == repeated.sha256
    assert revised.sha256 != first.sha256
    assert first.retrieved_at != repeated.retrieved_at
    assert len(list((tmp_path / "observations").glob("*.json"))) == 3
    assert len(list((tmp_path / "objects").glob("*/*"))) == 2
    assert archive.read(first.sha256) == b"original"
    assert archive.read(revised.sha256) == b"revised"


def test_archive_detects_corruption_and_rejects_paths(tmp_path: Path) -> None:
    archive = RawSourceArchive(tmp_path)
    result = archive.record(
        b"original",
        dataset="csd",
        source_id="1",
        source_name="../untrusted.zip",
        retrieved_at=datetime(2026, 11, 27, tzinfo=UTC),
    )
    object_path = tmp_path / "objects" / result.sha256[:2] / result.sha256
    object_path.write_bytes(b"corrupt")
    with pytest.raises(DataValidationError, match="integrity"):
        archive.read(result.sha256)
    with pytest.raises(ValueError, match="SHA-256"):
        archive.read("../untrusted.zip")


def test_archive_requires_aware_retrieval_time(tmp_path: Path) -> None:
    archive = RawSourceArchive(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        archive.record(
            b"data",
            dataset="csd",
            source_id="1",
            source_name="file.zip",
            retrieved_at=datetime(2026, 11, 27),
        )
