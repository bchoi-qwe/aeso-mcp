# SPDX-License-Identifier: MIT
"""One-shot bounded ingestion independent of the MCP transport."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

from aeso_mcp.app import build_container
from aeso_mcp.config import get_settings
from aeso_mcp.errors import AesoMcpError
from aeso_mcp.models.history import HistoricalDataset, HistoricalStoreSyncRequest


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected an ISO 8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("Timestamp must include an explicit UTC offset")
    return parsed


def parse_request(argv: list[str] | None = None) -> HistoricalStoreSyncRequest:
    """Require an explicit interval and dataset selection."""
    parser = argparse.ArgumentParser(
        prog="python -m aeso_mcp.ingest",
        description="Incrementally synchronize bounded AESO history into local storage.",
    )
    parser.add_argument("--start", type=_aware_datetime, required=True)
    parser.add_argument("--end", type=_aware_datetime, required=True)
    parser.add_argument(
        "--dataset",
        choices=[item.value for item in HistoricalDataset],
        action="append",
        required=True,
    )
    parser.add_argument("--generation-interval", choices=["hourly", "5-minute"], default="hourly")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    if args.start >= args.end:
        parser.error("--end must be after --start")
    if len(set(args.dataset)) != len(args.dataset):
        parser.error("--dataset must not contain duplicates")
    return HistoricalStoreSyncRequest(
        start=args.start,
        end=args.end,
        datasets=[HistoricalDataset(item) for item in args.dataset],
        generation_interval=args.generation_interval,
        refresh=args.refresh,
    )


async def ingest(request: HistoricalStoreSyncRequest) -> str:
    """Run ingestion using the same domain services as MCP, without starting a server."""
    container = build_container(get_settings())
    try:
        response = await container.history.sync_historical_store(request)
        return response.model_dump_json(indent=2)
    finally:
        await container.aclose()


def main(argv: list[str] | None = None) -> int:
    request = parse_request(argv)
    try:
        result = asyncio.run(ingest(request))
    except AesoMcpError as exc:
        sys.stderr.write(f"Ingestion failed: {exc.to_client_message()}\n")
        return 1
    sys.stdout.write(result + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
