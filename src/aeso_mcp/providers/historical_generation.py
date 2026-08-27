# SPDX-License-Identifier: MIT
"""Official AESO CSD generation archive adapter."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal

from aeso_mcp.errors import DataValidationError
from aeso_mcp.models.history import HistoricalGenerationInterval
from aeso_mcp.providers.archive_http import AesoArchiveHttpClient
from aeso_mcp.timeutil import MARKET_TZ, to_market, to_utc, utc_now

_SHARED_NAME = "qofgn9axnnw6uq3ip1goiq2ngb11txe5"
_FOLDERS: dict[str, str] = {"hourly": "196178549071", "5-minute": "196706124680"}
_CATALOG_URL = "https://aeso.box.com/s/{shared}/folder/{folder}?page={page}"
_DOWNLOAD_URL = (
    "https://aeso.app.box.com/index.php?rm=box_download_shared_file"
    "&shared_name={shared}&file_id=f_{file_id}"
)
_STREAM_RE = re.compile(r"Box\.postStreamData\s*=\s*(\{.*?\});", re.DOTALL)
_MONTH_RANGE_RE = re.compile(r"-\s*(\d{4})-(\d{2})(?:\s+to\s+(\d{4})-(\d{2}))?\.zip$")
_REQUIRED_COLUMNS = frozenset(
    {
        "Date (MST)",
        "Asset Short Name",
        "Asset Name",
        "Asset Grouping",
        "Volume",
        "Maximum Capability",
        "System Capability",
        "Fuel Type",
        "Sub Fuel Type",
        "Planning Area",
        "Region",
    }
)
_MST = timezone(timedelta(hours=-7), name="MST")
_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024


@dataclass(frozen=True)
class HistoricalGenerationSourceFile:
    """One immutable-or-replaceable Box archive object and its coverage."""

    file_id: str
    name: str
    size_bytes: int
    content_updated_at: datetime | None
    source_hash: str
    coverage_start: date
    coverage_end: date

    def intersects(self, start: datetime, end: datetime) -> bool:
        return (
            self.coverage_start < to_market(end).date() + timedelta(days=1)
            and self.coverage_end >= to_market(start).date()
        )


class HistoricalGenerationProvider:
    """Fetch and normalize hourly or five-minute individual-asset CSD history."""

    def __init__(self, http: AesoArchiveHttpClient) -> None:
        self._http = http

    async def list_source_files(
        self,
        interval: Literal["hourly", "5-minute"],
    ) -> list[HistoricalGenerationSourceFile]:
        folder = _FOLDERS[interval]
        files: list[HistoricalGenerationSourceFile] = []
        page = 1
        while True:
            html = await self._http.get_text(
                _CATALOG_URL.format(shared=_SHARED_NAME, folder=folder, page=page)
            )
            payload = _parse_box_stream(html)
            folder_data = payload.get("/app-api/enduserapp/shared-folder")
            if not isinstance(folder_data, dict):
                raise DataValidationError("AESO CSD Box page omitted shared-folder catalog data.")
            items = folder_data.get("items")
            if not isinstance(items, list):
                raise DataValidationError("AESO CSD Box catalog items were not a list.")
            for item in items:
                if not isinstance(item, dict) or item.get("type") != "file":
                    continue
                name = str(item.get("name", ""))
                coverage = _coverage_from_name(name)
                if coverage is None:
                    continue
                updated_raw = item.get("contentUpdated")
                files.append(
                    HistoricalGenerationSourceFile(
                        file_id=str(item.get("id", "")),
                        name=name,
                        size_bytes=int(item.get("itemSize") or 0),
                        content_updated_at=(
                            datetime.fromtimestamp(float(updated_raw), tz=UTC)
                            if updated_raw is not None
                            else None
                        ),
                        source_hash=str(item.get("fileIDHash") or ""),
                        coverage_start=coverage[0],
                        coverage_end=coverage[1],
                    )
                )
            page_count = int(folder_data.get("pageCount") or 1)
            if page >= page_count:
                break
            page += 1
        files.sort(key=lambda item: (item.coverage_start, item.file_id))
        return files

    async def fetch_source_file(
        self,
        source: HistoricalGenerationSourceFile,
        *,
        interval: Literal["hourly", "5-minute"],
        start: datetime,
        end: datetime,
        asset_ids: set[str] | None = None,
        fuel_types: set[str] | None = None,
    ) -> tuple[list[HistoricalGenerationInterval], str]:
        url = _DOWNLOAD_URL.format(shared=_SHARED_NAME, file_id=source.file_id)
        archive = await self._http.get_bytes(url)
        digest, rows = await asyncio.to_thread(_digest_and_read_archive, archive)
        retrieved_at = utc_now()
        duration = timedelta(hours=1) if interval == "hourly" else timedelta(minutes=5)
        result = await asyncio.to_thread(
            _parse_generation_rows,
            rows,
            source=source,
            interval=interval,
            duration=duration,
            retrieved_at=retrieved_at,
            start_utc=to_utc(start),
            end_utc=to_utc(end),
            asset_ids=asset_ids,
            fuel_types=fuel_types,
        )
        result.sort(key=lambda item: (item.interval_start_utc, item.asset_id))
        return result, digest


def _parse_box_stream(html: str) -> dict[str, object]:
    match = _STREAM_RE.search(html)
    if match is None:
        raise DataValidationError("AESO CSD Box page did not contain its catalog payload.")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise DataValidationError("AESO CSD Box catalog payload was invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise DataValidationError("AESO CSD Box catalog payload was not an object.")
    return payload


def _coverage_from_name(name: str) -> tuple[date, date] | None:
    match = _MONTH_RANGE_RE.search(name)
    if match is None:
        return None
    start_year, start_month = int(match.group(1)), int(match.group(2))
    end_year = int(match.group(3) or start_year)
    end_month = int(match.group(4) or start_month)
    start = date(start_year, start_month, 1)
    after_end = date(end_year + (end_month == 12), 1 if end_month == 12 else end_month + 1, 1)
    return start, after_end - timedelta(days=1)


def _read_single_csv(archive: bytes) -> list[dict[str, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = [
                item
                for item in bundle.infolist()
                if not item.is_dir() and item.filename.lower().endswith(".csv")
            ]
            if len(members) != 1:
                raise DataValidationError("AESO CSD ZIP must contain exactly one CSV file.")
            member = members[0]
            if member.file_size > _MAX_UNCOMPRESSED_BYTES:
                raise DataValidationError("AESO CSD CSV exceeded the 500 MiB safety limit.")
            with bundle.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                reader = csv.DictReader(text)
                fields = {field.strip() for field in (reader.fieldnames or [])}
                missing = sorted(_REQUIRED_COLUMNS - fields)
                if missing:
                    raise DataValidationError(
                        f"AESO CSD CSV omitted required columns: {', '.join(missing)}."
                    )
                return [dict(row) for row in reader]
    except zipfile.BadZipFile as exc:
        raise DataValidationError("AESO CSD download was not a valid ZIP archive.") from exc


def _digest_and_read_archive(archive: bytes) -> tuple[str, list[dict[str, str]]]:
    """Hash and decompress a potentially large archive off the event loop."""

    return hashlib.sha256(archive).hexdigest(), _read_single_csv(archive)


def _parse_generation_rows(
    rows: list[dict[str, str]],
    *,
    source: HistoricalGenerationSourceFile,
    interval: Literal["hourly", "5-minute"],
    duration: timedelta,
    retrieved_at: datetime,
    start_utc: datetime,
    end_utc: datetime,
    asset_ids: set[str] | None,
    fuel_types: set[str] | None,
) -> list[HistoricalGenerationInterval]:
    result: list[HistoricalGenerationInterval] = []
    for row in rows:
        parsed = _parse_generation_row(
            row,
            source=source,
            interval=interval,
            duration=duration,
            retrieved_at=retrieved_at,
        )
        if not (start_utc <= parsed.interval_start_utc < end_utc):
            continue
        if asset_ids and parsed.asset_id.upper() not in asset_ids:
            continue
        if fuel_types and parsed.fuel_type.upper() not in fuel_types:
            continue
        result.append(parsed)
    return result


def _parse_generation_row(
    row: dict[str, str],
    *,
    source: HistoricalGenerationSourceFile,
    interval: Literal["hourly", "5-minute"],
    duration: timedelta,
    retrieved_at: datetime,
) -> HistoricalGenerationInterval:
    try:
        fixed_mst = datetime.strptime(row["Date (MST)"].strip(), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=_MST
        )
        start_utc = fixed_mst.astimezone(UTC)
        local_start = start_utc.astimezone(MARKET_TZ)
        generation = float(row["Volume"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DataValidationError("AESO CSD row contained an invalid timestamp or volume.") from exc
    return HistoricalGenerationInterval(
        interval_start=local_start,
        interval_end=(start_utc + duration).astimezone(MARKET_TZ),
        interval_start_utc=start_utc,
        interval_end_utc=start_utc + duration,
        resolution=interval,
        asset_id=row["Asset Short Name"].strip(),
        asset_name=_text(row.get("Asset Name")),
        asset_grouping=_text(row.get("Asset Grouping")),
        fuel_type=row["Fuel Type"].strip(),
        sub_fuel_type=_text(row.get("Sub Fuel Type")),
        generation_mw=generation,
        maximum_capability_mw=_float(row.get("Maximum Capability")),
        system_capability_mw=_float(row.get("System Capability")),
        planning_area=_text(row.get("Planning Area")),
        region=_text(row.get("Region")),
        source_file_id=source.file_id,
        source_file_name=source.name,
        source_updated_at=source.content_updated_at,
        source_retrieved_at=retrieved_at,
    )


def _text(value: str | None) -> str | None:
    stripped = value.strip() if value else ""
    return stripped or None


def _float(value: str | None) -> float | None:
    stripped = value.strip().replace(",", "") if value else ""
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError as exc:
        raise DataValidationError(f"AESO CSD numeric value was invalid: {stripped!r}.") from exc
