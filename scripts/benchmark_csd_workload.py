#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Measure a complete recent public CSD archive on an ephemeral local store."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import resource
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.providers.historical_generation import HistoricalGenerationProvider
from aeso_mcp.storage.history import HistoricalStore
from aeso_mcp.storage.raw_archive import RawSourceArchive
from aeso_mcp.timeutil import start_of_market_day


def rss_kib() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def store_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


async def benchmark(
    root: Path, resolution: Literal["hourly", "5-minute"] = "hourly"
) -> dict[str, object]:
    container = build_container(Settings(history_store_path=root))
    try:
        archive = RawSourceArchive(root / "raw")
        provider = HistoricalGenerationProvider(container.archive_http, archive)
        sources = await provider.list_source_files(resolution)
        source = max(sources, key=lambda item: item.coverage_end)
        start = start_of_market_day(source.coverage_start)
        end = start_of_market_day(source.coverage_end + timedelta(days=1))
        t0 = perf_counter()
        publication = await provider.download_source(source, interval=resolution)
        digest = publication.digest
        download_seconds = perf_counter() - t0
        peak_after_download = rss_kib()
        store = HistoricalStore(root)
        t0 = perf_counter()
        received, written, partitions = await asyncio.to_thread(
            store.replace_generation_source_stream,
            publication.iter_records(start=start, end=end),
            source_file_id=source.file_id,
            source_file_name=source.name,
            source_hash=digest,
            source_updated_at=source.content_updated_at,
        )
        parse_write_seconds = perf_counter() - t0
        peak_after_write = rss_kib()
        t0 = perf_counter()
        page, total = store.query_generation(
            start=start,
            end=end,
            resolution=resolution,
            asset_ids=[],
            fuel_types=[],
            offset=0,
            limit=500,
        )
        query_seconds = perf_counter() - t0
        if total != written or len(page) != min(500, total):
            raise RuntimeError("Stored CSD count differs from normalized unique observations")
        return {
            "scope": "Actual complete AESO archive; Linux runner, cumulative process peak RSS",
            "resolution": resolution,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "platform": platform.platform(),
            "source_file": source.name,
            "source_sha256": digest,
            "compressed_bytes": (root / "raw" / "objects" / digest[:2] / digest).stat().st_size,
            "records_read": received,
            "records_written": written,
            "partitions": partitions,
            "download_seconds": download_seconds,
            "stream_parse_write_seconds": parse_write_seconds,
            "query_page_seconds": query_seconds,
            "peak_after_download_kib": peak_after_download,
            "peak_after_write_kib": peak_after_write,
            "peak_rss_kib": rss_kib(),
            "store_bytes": await asyncio.to_thread(store_bytes, root),
        }
    finally:
        await container.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", choices=["hourly", "5-minute"], default="hourly")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="aeso-workload-") as directory:
        result = asyncio.run(benchmark(Path(directory), args.resolution))
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
