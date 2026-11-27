#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Measure a complete recent public hourly CSD archive on an ephemeral local store."""

from __future__ import annotations

import asyncio
import json
import platform
import resource
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.providers.historical_generation import HistoricalGenerationProvider
from aeso_mcp.storage.history import HistoricalStore
from aeso_mcp.storage.raw_archive import RawSourceArchive
from aeso_mcp.timeutil import start_of_market_day


def rss_kib() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


async def benchmark(root: Path) -> dict[str, object]:
    container = build_container(Settings(history_store_path=root))
    try:
        archive = RawSourceArchive(root / "raw")
        provider = HistoricalGenerationProvider(container.archive_http, archive)
        sources = await provider.list_source_files("hourly")
        source = max(sources, key=lambda item: item.coverage_end)
        start = start_of_market_day(source.coverage_start)
        end = start_of_market_day(source.coverage_end + timedelta(days=1))
        t0 = perf_counter()
        rows, digest = await provider.fetch_source_file(
            source, interval="hourly", start=start, end=end
        )
        parse_seconds = perf_counter() - t0
        peak_after_parse = rss_kib()
        if not rows:
            raise RuntimeError("Live CSD archive returned no rows")
        store = HistoricalStore(root)
        t0 = perf_counter()
        written, partitions = store.replace_generation_source(
            rows,
            source_file_id=source.file_id,
            source_file_name=source.name,
            source_hash=digest,
            source_updated_at=source.content_updated_at,
        )
        write_seconds = perf_counter() - t0
        peak_after_write = rss_kib()
        t0 = perf_counter()
        page, total = store.query_generation(
            start=start, end=end, resolution="hourly",
            asset_ids=[], fuel_types=[], offset=0, limit=500,
        )
        query_seconds = perf_counter() - t0
        if total != written or len(page) != min(500, total):
            raise RuntimeError("Stored CSD count differs from normalized unique observations")
        return {
            "scope": "Actual AESO hourly archive; Linux runner, cumulative process peak RSS",
            "platform": platform.platform(),
            "source_file": source.name,
            "source_sha256": digest,
            "compressed_bytes": (root / "raw" / "objects" / digest[:2] / digest).stat().st_size,
            "records_read": len(rows),
            "records_written": written,
            "partitions": partitions,
            "download_archive_normalize_seconds": parse_seconds,
            "store_write_seconds": write_seconds,
            "query_page_seconds": query_seconds,
            "peak_after_parse_kib": peak_after_parse,
            "peak_after_write_kib": peak_after_write,
            "peak_rss_kib": rss_kib(),
            "store_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        }
    finally:
        await container.aclose()


def main() -> None:
    with TemporaryDirectory(prefix="aeso-workload-") as directory:
        result = asyncio.run(benchmark(Path(directory)))
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
