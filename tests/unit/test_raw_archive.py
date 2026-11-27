# SPDX-License-Identifier: MIT
"""Content identity, chronology and integrity of archived source objects."""

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from aeso_mcp.errors import DataValidationError, InvalidDateRangeError
from aeso_mcp.providers.historical_generation import replay_archived_generation
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


def _csd_zip(volume: int) -> bytes:
    raw = BytesIO()
    with ZipFile(raw, "w") as z:
        z.writestr(
            "csd.csv",
            "Date (MST),Asset Short Name,Asset Name,Asset Grouping,Volume,Maximum Capability,"
            "System Capability,Fuel Type,Sub Fuel Type,Planning Area,Region\n"
            f"2025-11-02 00:00:00,A,Asset,Group,{volume},100,100,WIND,,,\n"
            f"2025-11-02 01:00:00,A,Asset,Group,{volume + 1},100,100,WIND,,,\n",
        )
    return raw.getvalue()


def test_offline_csd_replay_retains_revision_and_dst_identity(tmp_path: Path) -> None:
    archive = RawSourceArchive(tmp_path)
    start = datetime(2025, 11, 2, 7, tzinfo=UTC)
    end = start + timedelta(hours=2)
    first_time = end + timedelta(days=1)
    first = archive.record(
        _csd_zip(10),
        dataset="historical_generation",
        source_id="1",
        source_name="CSD - 2025-11.zip",
        retrieved_at=first_time,
        source_variant="hourly",
    )
    # Later retrieval of a corrected publication cannot enter an earlier replay.
    archive.record(
        _csd_zip(20),
        dataset="historical_generation",
        source_id="1",
        source_name="CSD - 2025-11.zip",
        retrieved_at=first_time + timedelta(days=1),
        source_updated_at=first_time - timedelta(hours=1),
        source_variant="hourly",
    )
    early, missing = replay_archived_generation(
        archive, source_id="1", interval="hourly", start=start, end=end, as_of=end
    )
    assert early == [] and missing is None
    records, observation = replay_archived_generation(
        archive, source_id="1", interval="hourly", start=start, end=end, as_of=first_time
    )
    assert observation == first
    assert [r.generation_mw for r in records] == [10, 11]
    assert [r.interval_start_utc for r in records] == [start, start + timedelta(hours=1)]
    assert [r.interval_start.fold for r in records] == [0, 1]
    assert all(r.source_retrieved_at == first_time for r in records)
    later, _ = replay_archived_generation(
        archive,
        source_id="1",
        interval="hourly",
        start=start,
        end=end,
        as_of=first_time + timedelta(days=1),
    )
    assert [r.generation_mw for r in later] == [20, 21]
    with pytest.raises(InvalidDateRangeError, match="beyond as_of"):
        replay_archived_generation(
            archive,
            source_id="1",
            interval="hourly",
            start=start,
            end=end,
            as_of=start + timedelta(hours=1),
        )


def test_archive_select_rejects_ambiguous_latest_and_corrupted_manifest(tmp_path: Path) -> None:
    archive = RawSourceArchive(tmp_path)
    now = datetime(2026, 11, 27, tzinfo=UTC)
    for payload in (b"one", b"two"):
        archive.record(
            payload,
            dataset="csd",
            source_id="1",
            source_name="file.zip",
            retrieved_at=now,
        )
    with pytest.raises(DataValidationError, match="Ambiguous"):
        archive.select_as_of(dataset="csd", source_id="1", as_of=now)
    latest = archive.record(
        b"three",
        dataset="csd",
        source_id="1",
        source_name="file.zip",
        retrieved_at=now + timedelta(hours=1),
    )
    assert archive.select_as_of(
        dataset="csd", source_id="1", as_of=now + timedelta(hours=1)
    ) == latest
    next((tmp_path / "observations").glob("*.json")).write_bytes(b"{}")
    with pytest.raises(DataValidationError, match="manifest integrity"):
        archive.select_as_of(dataset="csd", source_id="1", as_of=now)
