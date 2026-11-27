#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Audit coverage and skipped entries in the fixed public CSD archive folders."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from typing import Any

from aeso_mcp.config import Settings
from aeso_mcp.providers.archive_http import AesoArchiveHttpClient
from aeso_mcp.providers.historical_generation import (
    _CATALOG_URL,
    _FOLDERS,
    _SHARED_NAME,
    _coverage_from_name,
    _parse_box_stream,
)


async def audit() -> dict[str, Any]:
    http = AesoArchiveHttpClient(Settings())
    try:
        result = {}
        for interval, folder in _FOLDERS.items():
            entries = []
            skipped = []
            pages = []
            for page in range(1, 101):
                html = await http.get_text(
                    _CATALOG_URL.format(shared=_SHARED_NAME, folder=folder, page=page)
                )
                data = _parse_box_stream(html).get("/app-api/enduserapp/shared-folder")
                if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                    raise ValueError("Unexpected public CSD catalog schema")
                pages.append(
                    {
                        "page": page,
                        "items": len(data["items"]),
                        "pageCount": data.get("pageCount"),
                        "totalCount": data.get("totalCount"),
                        "keys": sorted(data),
                    }
                )
                for item in data["items"]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", ""))
                    coverage = _coverage_from_name(name) if item.get("type") == "file" else None
                    if coverage is None:
                        skipped.append({"type": item.get("type"), "name": name})
                    else:
                        entries.append((coverage[0], coverage[1], name))
                if page >= int(data.get("pageCount") or 1):
                    break
            else:
                raise ValueError("Public CSD catalog exceeds 100 audit pages")
            entries.sort()
            gaps = []
            cursor = None
            for first, last, _ in entries:
                if cursor is not None and first > cursor + timedelta(days=1):
                    gaps.append(
                        {
                            "start": (cursor + timedelta(days=1)).isoformat(),
                            "end": (first - timedelta(days=1)).isoformat(),
                        }
                    )
                cursor = last if cursor is None else max(last, cursor)
            result[interval] = {
                "pages": pages,
                "recognized_files": len(entries),
                "earliest_coverage": entries[0][0].isoformat() if entries else None,
                "latest_coverage": cursor.isoformat() if cursor else None,
                "latest_files": [
                    {"name": name, "start": first.isoformat(), "end": last.isoformat()}
                    for first, last, name in entries[-8:]
                ],
                "coverage_gaps": gaps,
                "skipped_entries": skipped,
            }
        return result
    finally:
        await http.aclose()


def main() -> None:
    sys.stdout.write(json.dumps(asyncio.run(audit()), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
