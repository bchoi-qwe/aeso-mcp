# SPDX-License-Identifier: MIT
"""Credential-free adapters for fixed, official AESO research-data files.

The AESO data-request catalogue publishes a mixture of XLSX, ZIP/CSV, and
CSV files.  This module binds each parser to an exact official asset URL; it
does not accept user-controlled URLs or scrape an APIM-backed dataset.  XLSX
parsing is intentionally small and dependency-free so the runtime does not
need an optional spreadsheet engine.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import math
import re
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol, cast
from xml.etree import ElementTree

from aeso_mcp.errors import DataValidationError, InvalidDateRangeError, UnsupportedDatasetError
from aeso_mcp.models.common import ProviderName
from aeso_mcp.models.research_data import (
    ConstrainedVolumeInterval,
    EeaEventRecord,
    OrDirectiveRecord,
    PlanningAreaLoadGenerationInterval,
    PoolParticipantRecord,
    ResearchDataRequest,
    SupplyAdequacyHistoryInterval,
    SupplyCushionHistoryInterval,
    SystemFrequencyInterval,
    TransmissionOutageHistoryRecord,
)
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient
from aeso_mcp.timeutil import MARKET_TZ, add_elapsed, chronological_instant, to_market

# These URLs are the exact links currently published by the AESO data-request
# pages.  Keep them fixed and update deliberately if AESO replaces a file.
HISTORICAL_SUPPLY_ADEQUACY_URL = (
    "https://www.aeso.ca/assets/Uploads/market-and-system-reporting/"
    "Supply-Adequacy-Historical-Web-Codes-Jan-2019-to-Feb-2026.xlsx"
)
HISTORICAL_SUPPLY_CUSHION_URL = (
    "https://www.aeso.ca/assets/Uploads/market-and-system-reporting/"
    "Supply-Cushion-Historical-Web-Codes-Jun-2024-to-Feb-2026.xlsx"
)
HISTORICAL_TRANSMISSION_OUTAGES_URL = (
    "https://www.aeso.ca/assets/Uploads/data-requests/Transmission-Outages.xlsx"
)
CONSTRAINED_VOLUME_URL = (
    "https://www.aeso.ca/assets/Uploads/data-requests/"
    "Constrained-MW-by-Planning-Area-and-Fuel-Type.csv"
)
EEA_EVENTS_URL = "https://www.aeso.ca/assets/Uploads/data-requests/EEA-Events-2006-Nov_21_2024.xlsx"
OR_DIRECTIVES_URLS = (
    "https://www.aeso.ca/assets/Uploads/data-requests/OR-Directives-Jan-2024-to-July-2024.xlsx",
    "https://www.aeso.ca/assets/Uploads/data-requests/OR-Directives-Jan-2018-to-Feb-2023-v2.xlsx",
    "https://www.aeso.ca/assets/Uploads/data-requests/OR-Directives-Mar-2023-to-Aug-2023.xlsx",
    "https://www.aeso.ca/assets/Uploads/data-requests/OR-Directives-Sep-2023-to-Dec-2023.xlsx",
)
PLANNING_AREA_URL_TEMPLATE = (
    "https://www.aeso.ca/assets/Uploads/data-requests/Planning-Area-Load/"
    "Planning-Area-Load-Hourly-{year}.zip"
)

# The 10-second 2024 asset is a ZIP; the other 10-second assets are CSVs on
# the current AESO page.  Two-second assets are intentionally not exposed:
# their schema contains only an average value, unlike the verified 10-second
# average/minimum/maximum contract used by this server.
SYSTEM_FREQUENCY_URLS: dict[int, str] = {
    2019: "https://www.aeso.ca/assets/Uploads/data-requests/AESO-System-Frequency-2019.csv",
    2020: "https://www.aeso.ca/assets/Uploads/data-requests/AESO-System-Frequency-2020.csv",
    2021: "https://www.aeso.ca/assets/Uploads/data-requests/AESO-System-Frequency-2021.csv",
    2022: "https://www.aeso.ca/assets/Uploads/data-requests/AESO-System-Frequency-2022.csv",
    2023: "https://www.aeso.ca/assets/Uploads/data-requests/AESO-System-Frequency-2023.csv",
    2024: "https://www.aeso.ca/assets/Uploads/data-requests/AESO-System-10-Second-Frequency-2024.zip",
    2025: "https://www.aeso.ca/assets/Uploads/data-requests/AESO-System-10-Second-Frequency-2025.csv",
}

_MST = timezone(timedelta(hours=-7), name="MST")
_ONE_HOUR = timedelta(hours=1)
_TEN_SECONDS = timedelta(seconds=10)
_CSV_ENCODING = "utf-8-sig"
# The official 2024 annual frequency member is 301,000,235 bytes. The HTTP client
# bounds the downloaded asset separately; this cap prevents an allow-listed archive
# member from expanding without limit in the streaming CSV/XLSX parsers.
_MAX_ARCHIVE_MEMBER_BYTES = 384 * 1024 * 1024

_PUBLICATION_DATES: dict[str, str] = {
    "supply_adequacy": "2026-03-04",
    "supply_cushion": "2026-03-04",
    "transmission_outages": "2026-04-08",
    "planning_area": "2026-04-08",
    "constrained_volume": "2026-06-16",
    "eea_events": "2024-11-21",
    "or_directives": "2024-09-04",
    "system_frequency": "2025-07-10",
}


class PoolParticipantProvider(Protocol):
    async def get_pool_participants(
        self,
        *,
        pool_participant_ids: list[str],
        pool_participant_name: str | None,
    ) -> tuple[list[PoolParticipantRecord], dict[str, str]]: ...


class AesoResearchDataProvider:
    """Fetch and validate the named AESO research-data publications."""

    def __init__(
        self,
        http: AesoPublicReportsHttpClient,
        *,
        participant_provider: PoolParticipantProvider | None = None,
    ) -> None:
        self._http = http
        self._participant_provider = participant_provider

    async def get_research_data(
        self,
        request: ResearchDataRequest,
    ) -> tuple[list[Any], dict[str, object]]:
        """Return one validated dataset, filtered only after source parsing."""
        start, end = _request_range(request)
        if request.dataset == "supply_adequacy":
            return await self.get_supply_adequacy_history(start=start, end=end)
        if request.dataset == "supply_cushion":
            return await self.get_supply_cushion_history(start=start, end=end)
        if request.dataset == "transmission_outages":
            return await self.get_transmission_outage_history(start=start, end=end)
        if request.dataset == "planning_area":
            return await self.get_planning_area_history(
                start=start,
                end=end,
                planning_area=request.planning_area,
                region=request.region,
            )
        if request.dataset == "constrained_volume":
            return await self.get_constrained_volume_history(
                start=start,
                end=end,
                planning_area=request.planning_area,
                fuel_type=request.fuel_type,
            )
        if request.dataset == "eea_events":
            return await self.get_eea_event_history(start=start, end=end)
        if request.dataset == "or_directives":
            return await self.get_or_directive_history(start=start, end=end)
        if request.dataset == "system_frequency":
            return await self.get_system_frequency_history(start=start, end=end)
        if request.dataset == "pool_participants":
            if self._participant_provider is None:
                raise UnsupportedDatasetError("Pool Participant API provider is not configured.")
            (
                records,
                participant_provenance,
            ) = await self._participant_provider.get_pool_participants(
                pool_participant_ids=request.pool_participant_ids,
                pool_participant_name=request.pool_participant_name,
            )
            provenance: dict[str, object] = {}
            provenance.update(participant_provenance)
            return records, provenance
        raise UnsupportedDatasetError(f"Research dataset {request.dataset!r} is not implemented.")

    async def get_supply_adequacy_history(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[SupplyAdequacyHistoryInterval], dict[str, object]]:
        raw, provenance = await self._asset(HISTORICAL_SUPPLY_ADEQUACY_URL, "supply_adequacy")
        rows = cast(
            list[SupplyAdequacyHistoryInterval],
            _parse_web_code_xlsx(raw, dataset="supply_adequacy"),
        )
        return _filter_time(rows, start, end), provenance

    async def get_supply_cushion_history(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[SupplyCushionHistoryInterval], dict[str, object]]:
        raw, provenance = await self._asset(HISTORICAL_SUPPLY_CUSHION_URL, "supply_cushion")
        rows = cast(
            list[SupplyCushionHistoryInterval],
            _parse_web_code_xlsx(raw, dataset="supply_cushion"),
        )
        return _filter_time(rows, start, end), provenance

    async def get_transmission_outage_history(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[TransmissionOutageHistoryRecord], dict[str, object]]:
        raw, provenance = await self._asset(
            HISTORICAL_TRANSMISSION_OUTAGES_URL,
            "transmission_outages",
        )
        rows, skipped_rows = _parse_transmission_outages_xlsx(raw)
        if skipped_rows:
            provenance["source_unbounded_rows"] = skipped_rows
        return _filter_time(rows, start, end), provenance

    async def get_planning_area_history(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        planning_area: str | None = None,
        region: str | None = None,
    ) -> tuple[list[PlanningAreaLoadGenerationInterval], dict[str, object]]:
        years = _years_for_range(start, end, earliest=2015, latest=2025)
        rows: list[PlanningAreaLoadGenerationInterval] = []
        provenance: dict[str, object] = {
            "provider": ProviderName.AESO_PUBLIC_REPORT.value,
            "source_product": "Planning Area - Hourly Load and Generation",
            "source_file_ids": [],
            "source_file_names": [],
            "source_urls": [],
            "source_publication_date": _PUBLICATION_DATES["planning_area"],
        }
        hashes: list[str] = []
        for year in years:
            raw, item_provenance = await self._asset(
                PLANNING_AREA_URL_TEMPLATE.format(year=year),
                "planning_area",
            )
            rows.extend(
                await asyncio.to_thread(
                    _parse_planning_area_zip,
                    raw,
                    start=start,
                    end=end,
                    planning_area=planning_area,
                    region=region,
                )
            )
            _merge_asset_provenance(provenance, item_provenance)
            hash_value = item_provenance.get("source_hash")
            if isinstance(hash_value, str):
                hashes.append(hash_value)
        if hashes:
            provenance["source_hash"] = _combined_hash(hashes)
        rows.sort(
            key=lambda row: (
                chronological_instant(row.interval_start),
                row.region,
                row.planning_area,
            )
        )
        return rows, provenance

    async def get_constrained_volume_history(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        planning_area: str | None = None,
        fuel_type: str | None = None,
    ) -> tuple[list[ConstrainedVolumeInterval], dict[str, object]]:
        raw, provenance = await self._asset(CONSTRAINED_VOLUME_URL, "constrained_volume")
        rows = _parse_constrained_csv(
            raw,
            start=start,
            end=end,
            planning_area=planning_area,
            fuel_type=fuel_type,
        )
        return rows, provenance

    async def get_eea_event_history(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[EeaEventRecord], dict[str, object]]:
        raw, provenance = await self._asset(EEA_EVENTS_URL, "eea_events")
        rows = _parse_eea_xlsx(raw)
        return _filter_time(rows, start, end), provenance

    async def get_or_directive_history(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[OrDirectiveRecord], dict[str, object]]:
        rows: list[OrDirectiveRecord] = []
        provenance: dict[str, object] = {
            "provider": ProviderName.AESO_PUBLIC_REPORT.value,
            "source_product": "Aggregated Contingency Reserve Directive Quantities",
            "source_file_ids": [],
            "source_file_names": [],
            "source_urls": [],
            "source_publication_date": _PUBLICATION_DATES["or_directives"],
        }
        hashes: list[str] = []
        for url in OR_DIRECTIVES_URLS:
            raw, item_provenance = await self._asset(url, "or_directives")
            rows.extend(_parse_or_directives_xlsx(raw, start=start, end=end))
            _merge_asset_provenance(provenance, item_provenance)
            hash_value = item_provenance.get("source_hash")
            if isinstance(hash_value, str):
                hashes.append(hash_value)
        if hashes:
            provenance["source_hash"] = _combined_hash(hashes)
        rows = _deduplicate_or_directives(rows)
        rows.sort(
            key=lambda row: (
                chronological_instant(row.interval_start),
                row.event_number,
                row.service,
            )
        )
        return rows, provenance

    async def get_system_frequency_history(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[SystemFrequencyInterval], dict[str, object]]:
        if start is None or end is None:
            raise InvalidDateRangeError(
                "System frequency retrieval requires a bounded start/end window (maximum 6 hours)."
            )
        if chronological_instant(end) - chronological_instant(start) > timedelta(hours=6):
            raise InvalidDateRangeError("System frequency raw windows may not exceed 6 hours.")
        years = _years_for_range(start, end, earliest=2019, latest=2025)
        rows: list[SystemFrequencyInterval] = []
        provenance: dict[str, object] = {
            "provider": ProviderName.AESO_PUBLIC_REPORT.value,
            "source_product": "System Frequency (10-second)",
            "source_file_ids": [],
            "source_file_names": [],
            "source_urls": [],
            "source_publication_date": _PUBLICATION_DATES["system_frequency"],
            "frequency_resolution": "10s",
        }
        hashes: list[str] = []
        for year in years:
            url = SYSTEM_FREQUENCY_URLS.get(year)
            if url is None:
                raise UnsupportedDatasetError(
                    f"AESO 10-second system-frequency data is unavailable for {year}."
                )
            raw, item_provenance = await self._asset(url, "system_frequency")
            rows.extend(await asyncio.to_thread(_parse_frequency_asset, raw, start=start, end=end))
            _merge_asset_provenance(provenance, item_provenance)
            hash_value = item_provenance.get("source_hash")
            if isinstance(hash_value, str):
                hashes.append(hash_value)
        if hashes:
            provenance["source_hash"] = _combined_hash(hashes)
        rows.sort(key=lambda row: chronological_instant(row.interval_start))
        return rows, provenance

    async def _asset(self, url: str, dataset: str) -> tuple[bytes, dict[str, object]]:
        raw = await self._http.get_binary_asset(url)
        filename = PurePosixPath(url.split("?", 1)[0]).name
        return raw, {
            "provider": ProviderName.AESO_PUBLIC_REPORT.value,
            "source_product": _source_product(dataset),
            "source_url": url,
            "source_file_id": url,
            "source_file_name": filename,
            "source_hash": f"sha256:{hashlib.sha256(raw).hexdigest()}",
            "source_publication_date": _PUBLICATION_DATES.get(dataset),
        }


def _source_product(dataset: str) -> str:
    return {
        "supply_adequacy": "Historical Supply Adequacy Web Codes",
        "supply_cushion": "Historical Supply Cushion Web Codes",
        "transmission_outages": "Historical Transmission Outages",
        "planning_area": "Planning Area - Hourly Load and Generation",
        "constrained_volume": "Constrained Volume by Planning Area and Fuel Type",
        "eea_events": "Historical Energy Emergency Alert Event Data",
        "or_directives": "Aggregated Contingency Reserve Directive Quantities",
        "system_frequency": "System Frequency (10-second)",
    }.get(dataset, dataset)


def _request_range(request: ResearchDataRequest) -> tuple[datetime | None, datetime | None]:
    if request.start is None or request.end is None:
        return None, None
    from aeso_mcp.timeutil import validate_range

    return validate_range(
        request.start, request.end, max_days=366, label=f"{request.dataset} range"
    )


def _years_for_range(
    start: datetime | None,
    end: datetime | None,
    *,
    earliest: int,
    latest: int,
) -> list[int]:
    if start is None or end is None:
        return list(range(earliest, latest + 1))
    first = max(to_market(start).year, earliest)
    last = min(to_market(end - timedelta(microseconds=1)).year, latest)
    if last < first:
        return []
    return list(range(first, last + 1))


def _filter_time[T](
    rows: Iterable[T],
    start: datetime | None,
    end: datetime | None,
) -> list[T]:
    if start is None or end is None:
        return list(rows)
    start_i = chronological_instant(start)
    end_i = chronological_instant(end)
    result: list[T] = []
    for row in rows:
        interval = getattr(row, "interval_start", None)
        if isinstance(interval, datetime) and start_i <= chronological_instant(interval) < end_i:
            result.append(row)
    return result


def _parse_web_code_xlsx(
    raw: bytes,
    *,
    dataset: Literal["supply_adequacy", "supply_cushion"],
) -> list[SupplyAdequacyHistoryInterval | SupplyCushionHistoryInterval]:
    required = {
        "BEGIN_DATE_GMT",
        "BEGIN_DATE_LOCAL",
        "VERSION_START_TIME_GMT",
        "WEB_CODE",
    }
    rows = _xlsx_rows(raw, required, _source_product(dataset))
    parsed: list[SupplyAdequacyHistoryInterval | SupplyCushionHistoryInterval] = []
    seen: set[datetime] = set()
    for row in rows:
        start = _parse_timestamp(row["BEGIN_DATE_GMT"], "BEGIN_DATE_GMT", zone="utc")
        local_start = _parse_timestamp(row["BEGIN_DATE_LOCAL"], "BEGIN_DATE_LOCAL", zone="market")
        version = _parse_timestamp(
            row["VERSION_START_TIME_GMT"],
            "VERSION_START_TIME_GMT",
            zone="utc",
        )
        # Parse the local columns too so a source-shape drift cannot silently
        # pass with only one side of the documented timestamp pair.
        local_version = row.get("VERSION_START_LOCAL") or row.get("VERSION_START_TIME_LOCAL")
        local_version_timestamp = _parse_timestamp(
            local_version,
            "VERSION_START_TIME_LOCAL",
            zone="market",
        )
        if local_version_timestamp is None:
            raise DataValidationError(
                f"{_source_product(dataset)} has a missing local version timestamp."
            )
        if start is None or local_start is None or version is None:
            raise DataValidationError(f"{_source_product(dataset)} has a missing timestamp.")
        if (
            abs((chronological_instant(start) - chronological_instant(local_start)).total_seconds())
            > 3600
        ):
            raise DataValidationError(f"{_source_product(dataset)} GMT/local timestamps disagree.")
        if (
            abs(
                (
                    chronological_instant(version) - chronological_instant(local_version_timestamp)
                ).total_seconds()
            )
            > 3600
        ):
            raise DataValidationError(
                f"{_source_product(dataset)} version GMT/local timestamps disagree."
            )
        code = _required_int(row["WEB_CODE"], f"{_source_product(dataset)} WEB_CODE")
        key = chronological_instant(start)
        if key in seen:
            raise DataValidationError(f"{_source_product(dataset)} has duplicate historical hour.")
        seen.add(key)
        if dataset == "supply_adequacy":
            parsed.append(
                SupplyAdequacyHistoryInterval(
                    interval_start=start,
                    interval_end=add_elapsed(start, _ONE_HOUR),
                    version_start=version,
                    web_code=code,
                )
            )
        else:
            parsed.append(
                SupplyCushionHistoryInterval(
                    interval_start=start,
                    interval_end=add_elapsed(start, _ONE_HOUR),
                    version_start=version,
                    web_code=code,
                )
            )
    if not parsed:
        raise DataValidationError(f"{_source_product(dataset)} contained no observations.")
    parsed.sort(key=lambda row: chronological_instant(row.interval_start))
    return parsed


def _parse_transmission_outages_xlsx(
    raw: bytes,
) -> tuple[list[TransmissionOutageHistoryRecord], int]:
    required = {
        "Owner",
        "Name",
        "Outage Start",
        "Planned End",
        "Tie Line",
        "Significant Outage",
        "Activity",
        "Equipment Type",
        "Outage Event",
        "Outage Reason",
        "Outage Condition",
    }
    rows = _xlsx_rows(raw, required, "Historical Transmission Outages")
    parsed: list[TransmissionOutageHistoryRecord] = []
    skipped_unbounded = 0
    for row in rows:
        owner = _required_text(row["Owner"], "Historical Transmission Outages Owner")
        name = _required_text(row["Name"], "Historical Transmission Outages Name")
        start = _parse_timestamp(row["Outage Start"], "Outage Start", zone="market")
        end = _parse_timestamp(row["Planned End"], "Planned End", zone="market")
        if start is None and end is None:
            skipped_unbounded += 1
            continue
        if start is None or end is None:
            raise DataValidationError("Historical Transmission Outages has a missing outage bound.")
        if chronological_instant(end) < chronological_instant(start):
            raise DataValidationError("Historical Transmission Outages has a negative interval.")
        parsed.append(
            TransmissionOutageHistoryRecord(
                interval_start=start,
                interval_end=end,
                owner=owner,
                name=name,
                tie_line=_optional_text(row["Tie Line"]),
                significant_outage=_optional_text(row["Significant Outage"]),
                activity=_optional_text(row["Activity"]),
                equipment_type=_optional_text(row["Equipment Type"]),
                outage_event=_optional_text(row["Outage Event"]),
                outage_reason=_optional_text(row["Outage Reason"]),
                outage_condition=_optional_text(row["Outage Condition"]),
            )
        )
    if not parsed:
        raise DataValidationError("Historical Transmission Outages contained no observations.")
    parsed.sort(key=lambda row: chronological_instant(row.interval_start))
    return parsed, skipped_unbounded


def _parse_planning_area_zip(
    raw: bytes,
    *,
    start: datetime | None,
    end: datetime | None,
    planning_area: str | None,
    region: str | None,
) -> list[PlanningAreaLoadGenerationInterval]:
    required = {
        "DT_MST",
        "DT_MPT",
        "Month",
        "REGION",
        "PLANNING_AREA",
        "Load",
        "SysGen",
        "CSDGen",
        "BTFGen",
        "ActLoad",
    }
    result: list[PlanningAreaLoadGenerationInterval] = []
    seen: set[tuple[datetime, str, str]] = set()
    for row in _zip_csv_rows(raw, required, "Planning Area - Hourly Load and Generation"):
        row_region = _required_text(row["REGION"], "Planning Area REGION")
        row_area = _required_text(row["PLANNING_AREA"], "Planning Area PLANNING_AREA")
        if region is not None and row_region.casefold() != region.strip().casefold():
            continue
        if planning_area is not None and row_area.casefold() != planning_area.strip().casefold():
            continue
        # DT_MST is fixed UTC-7 while DT_MPT is prevailing Mountain Time.
        # Using the fixed field preserves a unique instant across DST changes;
        # parse DT_MPT as well to validate the documented one-hour relationship.
        interval = _parse_timestamp(row["DT_MST"], "Planning Area DT_MST", zone="mst")
        mpt = _parse_timestamp(row["DT_MPT"], "Planning Area DT_MPT", zone="market")
        if interval is None or mpt is None:
            raise DataValidationError("Planning Area file has a missing timestamp.")
        wall_times_match = interval.replace(tzinfo=None) == mpt.replace(tzinfo=None)
        if (
            abs((chronological_instant(interval) - chronological_instant(mpt)).total_seconds()) > 1
            and not wall_times_match
        ):
            raise DataValidationError("Planning Area DT_MST/DT_MPT timestamps disagree.")
        if not _in_range(interval, start, end):
            continue
        key = (chronological_instant(interval), row_region, row_area)
        if key in seen:
            raise DataValidationError("Planning Area file has duplicate interval rows.")
        seen.add(key)
        result.append(
            PlanningAreaLoadGenerationInterval(
                interval_start=interval,
                interval_end=add_elapsed(interval, _ONE_HOUR),
                region=row_region,
                planning_area=row_area,
                load_mw=_optional_float(row["Load"], "Planning Area Load"),
                system_generation_mw=_optional_float(row["SysGen"], "Planning Area SysGen"),
                csd_generation_mw=_optional_float(row["CSDGen"], "Planning Area CSDGen"),
                behind_the_fence_generation_mw=_optional_float(
                    row["BTFGen"], "Planning Area BTFGen"
                ),
                actual_load_mw=_optional_float(row["ActLoad"], "Planning Area ActLoad"),
            )
        )
    result.sort(
        key=lambda row: (chronological_instant(row.interval_start), row.region, row.planning_area)
    )
    return result


def _parse_constrained_csv(
    raw: bytes,
    *,
    start: datetime | None,
    end: datetime | None,
    planning_area: str | None,
    fuel_type: str | None,
) -> list[ConstrainedVolumeInterval]:
    required = {
        "Date (GMT)",
        "Date (MPT)",
        "Fuel Type",
        "Planning Area",
        "Constrained_MW",
        "Minutes",
    }
    text = io.TextIOWrapper(io.BytesIO(raw), encoding=_CSV_ENCODING, errors="strict", newline="")
    reader = csv.DictReader(text)
    _require_columns(reader.fieldnames, required, "Constrained Volume")
    result: list[ConstrainedVolumeInterval] = []
    seen: set[tuple[datetime, str, str]] = set()
    for raw_row in reader:
        row = _clean_row(raw_row)
        if not any(value for value in row.values()):
            continue
        interval = _parse_timestamp(row["Date (GMT)"], "Constrained Volume Date (GMT)", zone="utc")
        mpt = _parse_timestamp(row["Date (MPT)"], "Constrained Volume Date (MPT)", zone="market")
        if interval is None or mpt is None:
            raise DataValidationError("Constrained Volume has a missing timestamp.")
        if (
            abs((chronological_instant(interval) - chronological_instant(mpt)).total_seconds())
            > 3600
        ):
            raise DataValidationError("Constrained Volume GMT/MPT timestamps disagree.")
        if not _in_range(interval, start, end):
            continue
        row_fuel = _required_text(row["Fuel Type"], "Constrained Volume Fuel Type")
        row_area = _required_text(row["Planning Area"], "Constrained Volume Planning Area")
        if fuel_type is not None and row_fuel.casefold() != fuel_type.strip().casefold():
            continue
        if planning_area is not None and row_area.casefold() != planning_area.strip().casefold():
            continue
        # A single hour can contain multiple constrained rows (one per fuel
        # type/planning area); only the full source key is required to be
        # unique.
        key = (chronological_instant(interval), row_fuel, row_area)
        if key in seen:
            raise DataValidationError("Constrained Volume has duplicate UTC intervals.")
        seen.add(key)
        result.append(
            ConstrainedVolumeInterval(
                interval_start=interval,
                interval_end=add_elapsed(interval, _ONE_HOUR),
                fuel_type=row_fuel,
                planning_area=row_area,
                constrained_volume_mwh=_required_float(
                    row["Constrained_MW"], "Constrained Volume Constrained_MW"
                ),
                constrained_minutes=_required_int(row["Minutes"], "Constrained Volume Minutes"),
            )
        )
    result.sort(key=lambda row: chronological_instant(row.interval_start))
    return result


def _parse_eea_xlsx(raw: bytes) -> list[EeaEventRecord]:
    required = {
        "Year",
        "Count of Events",
        "Month",
        "Quarter",
        "Start Date",
        "End Date",
        "Length in Hours",
        "length in minutes",
        "Highest Level Declared",
        "Comment(s)",
    }
    rows = _xlsx_rows(raw, required, "Historical EEA Event Data")
    result: list[EeaEventRecord] = []
    seen: set[tuple[datetime, datetime, str]] = set()
    for row in rows:
        start = _parse_timestamp(row["Start Date"], "EEA Start Date", zone="market")
        end = _parse_timestamp(row["End Date"], "EEA End Date", zone="market")
        level = _required_text(row["Highest Level Declared"], "EEA Highest Level Declared")
        if start is None or end is None:
            raise DataValidationError("Historical EEA Event Data has a missing event bound.")
        if chronological_instant(end) <= chronological_instant(start):
            raise DataValidationError(
                "Historical EEA Event Data has a non-positive event duration."
            )
        key = (chronological_instant(start), chronological_instant(end), level)
        if key in seen:
            raise DataValidationError("Historical EEA Event Data has duplicate events.")
        seen.add(key)
        result.append(
            EeaEventRecord(
                interval_start=start,
                interval_end=end,
                eea_level=level,
                duration_hours=_required_float(row["Length in Hours"], "EEA Length in Hours"),
                duration_minutes=_required_float(row["length in minutes"], "EEA length in minutes"),
                comments=_optional_text(row["Comment(s)"]),
            )
        )
    if not result:
        raise DataValidationError("Historical EEA Event Data contained no observations.")
    result.sort(key=lambda row: chronological_instant(row.interval_start))
    return result


def _parse_or_directives_xlsx(
    raw: bytes,
    *,
    start: datetime | None,
    end: datetime | None,
) -> list[OrDirectiveRecord]:
    required = {
        "Event Number",
        "Timestamp",
        "Service",
        "Time-Weighted Average Directive (MW)",
        "Maximum Directive (MW)",
        "Event Duration (s)",
        "Energy (MWh)",
        "Max Duration within Event (s)",
        "Min Duration within Event (s)",
    }
    rows = _xlsx_rows(raw, required, "OR Directives")
    capacity_columns = {
        "Time-Weighted Average Capacity (MW)",
        "Capacity (MW)",
    }
    available_capacity_columns = capacity_columns.intersection(rows[0]) if rows else set()
    if not available_capacity_columns:
        raise DataValidationError(
            "OR Directives XLSX schema changed; missing one of: "
            "Time-Weighted Average Capacity (MW), Capacity (MW)."
        )
    result: list[OrDirectiveRecord] = []
    seen: set[tuple[int, datetime, str]] = set()
    for row in rows:
        event_number = _required_int(row["Event Number"], "OR Event Number")
        timestamp = _parse_timestamp(row["Timestamp"], "OR Timestamp", zone="market")
        service = _required_text(row["Service"], "OR Service")
        if timestamp is None:
            raise DataValidationError("OR Directives has a missing timestamp.")
        if not _in_range(timestamp, start, end):
            continue
        key = (event_number, chronological_instant(timestamp), service)
        if key in seen:
            raise DataValidationError("OR Directives has duplicate event/service rows.")
        seen.add(key)
        duration = _required_int(row["Event Duration (s)"], "OR Event Duration")
        result.append(
            OrDirectiveRecord(
                interval_start=timestamp,
                interval_end=add_elapsed(timestamp, timedelta(seconds=duration)),
                event_number=event_number,
                service=service,
                time_weighted_average_capacity_mw=(
                    _required_float(
                        row["Time-Weighted Average Capacity (MW)"], "OR average capacity"
                    )
                    if "Time-Weighted Average Capacity (MW)" in available_capacity_columns
                    else None
                ),
                capacity_mw=(
                    _required_float(row["Capacity (MW)"], "OR capacity")
                    if "Capacity (MW)" in available_capacity_columns
                    else None
                ),
                time_weighted_average_directive_mw=_required_float(
                    row["Time-Weighted Average Directive (MW)"], "OR average directive"
                ),
                maximum_directive_mw=_required_float(
                    row["Maximum Directive (MW)"], "OR maximum directive"
                ),
                event_duration_seconds=duration,
                energy_mwh=_required_float(row["Energy (MWh)"], "OR energy"),
                max_duration_seconds=_optional_float(
                    row["Max Duration within Event (s)"], "OR max duration"
                ),
                min_duration_seconds=_optional_float(
                    row["Min Duration within Event (s)"], "OR min duration"
                ),
            )
        )
    result.sort(
        key=lambda row: (chronological_instant(row.interval_start), row.event_number, row.service)
    )
    return result


def _deduplicate_or_directives(
    rows: Iterable[OrDirectiveRecord],
) -> list[OrDirectiveRecord]:
    """Remove exact rows repeated by overlapping published archive links.

    AESO currently serves identical content from two OR-directive links. A
    conflicting row with the same source identity is a publication/schema
    problem and must not be silently chosen between.
    """
    by_key: dict[tuple[int, datetime, str], OrDirectiveRecord] = {}
    for row in rows:
        key = (row.event_number, chronological_instant(row.interval_start), row.service)
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = row
        elif previous != row:
            raise DataValidationError(
                "OR Directives contain conflicting duplicate event/service rows."
            )
    return list(by_key.values())


def _parse_frequency_asset(
    raw: bytes,
    *,
    start: datetime,
    end: datetime,
) -> list[SystemFrequencyInterval]:
    required = {"Timestamp (UTC)", "Timestamp (MT)", "Avg", "Max", "Min"}
    result: list[SystemFrequencyInterval] = []
    seen: set[datetime] = set()
    for row in _asset_csv_rows(raw, required, "System Frequency"):
        interval = _parse_timestamp(
            row["Timestamp (UTC)"], "System Frequency UTC timestamp", zone="utc"
        )
        mt = _parse_timestamp(row["Timestamp (MT)"], "System Frequency MT timestamp", zone="market")
        if interval is None or mt is None:
            raise DataValidationError("System Frequency has a missing timestamp.")
        if (
            abs((chronological_instant(interval) - chronological_instant(mt)).total_seconds())
            > 3600
        ):
            raise DataValidationError("System Frequency UTC/MT timestamps disagree.")
        if not _in_range(interval, start, end):
            continue
        key = chronological_instant(interval)
        if key in seen:
            raise DataValidationError("System Frequency has duplicate UTC timestamps.")
        seen.add(key)
        result.append(
            SystemFrequencyInterval(
                interval_start=interval,
                interval_end=add_elapsed(interval, _TEN_SECONDS),
                average_frequency_hz=_required_float(row["Avg"], "System Frequency Avg"),
                maximum_frequency_hz=_required_float(row["Max"], "System Frequency Max"),
                minimum_frequency_hz=_required_float(row["Min"], "System Frequency Min"),
            )
        )
    result.sort(key=lambda row: chronological_instant(row.interval_start))
    return result


def _asset_csv_rows(raw: bytes, required: set[str], report: str) -> Iterator[dict[str, str | None]]:
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                if len(names) != 1:
                    raise DataValidationError(f"{report} ZIP must contain exactly one CSV file.")
                _validate_archive_member(archive.getinfo(names[0]), report)
                with archive.open(names[0], "r") as handle:
                    text = io.TextIOWrapper(
                        handle, encoding=_CSV_ENCODING, errors="strict", newline=""
                    )
                    reader = csv.DictReader(text)
                    _require_columns(reader.fieldnames, required, report)
                    for row in reader:
                        yield _clean_row(row)
        except zipfile.BadZipFile as exc:
            raise DataValidationError(f"{report} returned an invalid ZIP archive.") from exc
        return
    text = io.TextIOWrapper(io.BytesIO(raw), encoding=_CSV_ENCODING, errors="strict", newline="")
    reader = csv.DictReader(text)
    _require_columns(reader.fieldnames, required, report)
    for row in reader:
        yield _clean_row(row)


def _zip_csv_rows(raw: bytes, required: set[str], report: str) -> Iterator[dict[str, str | None]]:
    yield from _asset_csv_rows(raw, required, report)


def _xlsx_rows(raw: bytes, required: set[str], report: str) -> list[dict[str, str | None]]:
    if raw[:2] != b"PK":
        raise DataValidationError(f"{report} did not return an XLSX archive.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            sheet_names = [
                name
                for name in archive.namelist()
                if name.startswith("xl/worksheets/") and name.endswith(".xml")
            ]
            if not sheet_names:
                raise DataValidationError(f"{report} XLSX has no worksheet.")
            shared = _shared_strings(archive)
            sheet_name = sorted(sheet_names)[0]
            _validate_archive_member(archive.getinfo(sheet_name), report)
            root = _safe_xml_root(archive.read(sheet_name), report)
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise DataValidationError(f"{report} returned an invalid XLSX archive.") from exc

    rows: list[dict[str, str | None]] = []
    for row_element in root.iter(_xml_name("row")):
        values: dict[int, str | None] = {}
        for cell in row_element.findall(_xml_name("c")):
            reference = cell.attrib.get("r")
            if reference is None:
                continue
            column = _column_number(reference)
            values[column] = _xlsx_cell_value(cell, shared)
        if values:
            rows.append({str(index): value for index, value in values.items()})
    if not rows:
        raise DataValidationError(f"{report} XLSX contained no rows.")
    header_values = rows[0]
    headers = {
        index: value.strip()
        for index, value in ((int(k), v) for k, v in header_values.items())
        if value
    }
    normalized_headers = {value.strip() for value in headers.values()}
    missing = sorted(required - normalized_headers)
    if missing:
        raise DataValidationError(
            f"{report} XLSX schema changed; missing required column(s): {', '.join(missing)}."
        )
    if len(headers) != len(set(headers.values())):
        raise DataValidationError(f"{report} XLSX has duplicate column headers.")
    result: list[dict[str, str | None]] = []
    for raw_row in rows[1:]:
        row = {header: raw_row.get(str(index)) for index, header in headers.items()}
        if not any(value not in (None, "") for value in row.values()):
            continue
        result.append(row)
    return result


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        _validate_archive_member(archive.getinfo("xl/sharedStrings.xml"), "XLSX shared strings")
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = _safe_xml_root(raw, "XLSX shared strings")
    except ElementTree.ParseError as exc:
        raise DataValidationError("XLSX shared strings are malformed.") from exc
    return [
        "".join(node.text or "" for node in item.iter(_xml_name("t")))
        for item in root.iter(_xml_name("si"))
    ]


def _validate_archive_member(info: zipfile.ZipInfo, report: str) -> None:
    if info.is_dir() or info.file_size > _MAX_ARCHIVE_MEMBER_BYTES:
        raise DataValidationError(
            f"{report} archive member exceeded the 384 MiB uncompressed safety limit."
        )


def _safe_xml_root(raw: bytes, report: str) -> ElementTree.Element:
    """Parse an XLSX XML part without permitting DTD/entity expansion."""
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise DataValidationError(f"{report} contains a forbidden XML declaration.")
    # DTD/entity declarations are rejected above, and both compressed and
    # uncompressed member sizes are bounded before this parser is reached.
    parser = ElementTree.XMLParser()  # noqa: S314
    parser.feed(raw)
    return parser.close()


def _xlsx_cell_value(cell: ElementTree.Element, shared: list[str]) -> str | None:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        value = "".join(node.text or "" for node in cell.iter(_xml_name("t")))
        return value or None
    value_node = cell.find(_xml_name("v"))
    if value_node is None or value_node.text is None:
        return None
    value = value_node.text
    if cell_type == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError) as exc:
            raise DataValidationError("XLSX contains an invalid shared-string index.") from exc
    return value


def _xml_name(name: str) -> str:
    return f"{{http://schemas.openxmlformats.org/spreadsheetml/2006/main}}{name}"


def _column_number(reference: str) -> int:
    letters = re.match(r"[A-Za-z]+", reference)
    if letters is None:
        raise DataValidationError(f"XLSX cell reference is malformed: {reference!r}.")
    number = 0
    for character in letters.group(0).upper():
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def _parse_timestamp(value: str | None, label: str, *, zone: str) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    parsed: datetime | None = None
    try:
        serial = float(text)
    except ValueError:
        serial = None
    if serial is not None and serial > 1:
        parsed = datetime(1899, 12, 30) + timedelta(days=serial)
    else:
        normalized = text.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M",
                "%d-%b-%y %I.%M.%S.%f %p",
                "%d-%b-%y %I.%M.%S %p",
            ):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
    if parsed is None:
        raise DataValidationError(f"{label} has malformed timestamp {text!r}.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo={"utc": UTC, "market": MARKET_TZ, "mst": _MST}[zone])
    elif zone == "utc":
        parsed = parsed.astimezone(UTC)
    elif zone in {"market", "mst"}:
        parsed = parsed.astimezone(MARKET_TZ)
    return to_market(parsed)


def _in_range(value: datetime, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or chronological_instant(value) >= chronological_instant(start)) and (
        end is None or chronological_instant(value) < chronological_instant(end)
    )


def _clean_row(row: Mapping[str | None, str | None]) -> dict[str, str | None]:
    return {
        str(key).strip(): value.strip() if isinstance(value, str) else value
        for key, value in row.items()
        if key is not None
    }


def _require_columns(fieldnames: Iterable[str] | None, required: set[str], report: str) -> None:
    if fieldnames is None:
        raise DataValidationError(f"{report} CSV has no header row.")
    normalized = {name.strip() for name in fieldnames if name}
    missing = sorted(required - normalized)
    if missing:
        raise DataValidationError(
            f"{report} CSV schema changed; missing required column(s): {', '.join(missing)}."
        )


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text if text and text not in {"-", "—", "N/A", "n/a", "nan", "NaN"} else None


def _required_text(value: str | None, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise DataValidationError(f"{label} is missing from an AESO row.")
    return text


def _optional_float(value: str | None, label: str) -> float | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = float(text.replace(",", ""))
    except ValueError as exc:
        raise DataValidationError(f"{label} has malformed numeric value {text!r}.") from exc
    if not math.isfinite(parsed):
        raise DataValidationError(f"{label} has non-finite numeric value {text!r}.")
    return parsed


def _required_float(value: str | None, label: str) -> float:
    parsed = _optional_float(value, label)
    if parsed is None:
        raise DataValidationError(f"{label} is missing from an AESO row.")
    return parsed


def _required_int(value: str | None, label: str) -> int:
    parsed = _required_float(value, label)
    if not parsed.is_integer():
        raise DataValidationError(f"{label} must be an integer, got {parsed!r}.")
    return int(parsed)


def _merge_asset_provenance(target: dict[str, object], item: Mapping[str, object]) -> None:
    for singular, plural in (
        ("source_file_id", "source_file_ids"),
        ("source_file_name", "source_file_names"),
        ("source_url", "source_urls"),
    ):
        value = item.get(singular)
        values = target.setdefault(plural, [])
        if isinstance(value, str) and isinstance(values, list) and value not in values:
            values.append(value)


def _combined_hash(hashes: Iterable[str]) -> str:
    canonical = "\n".join(sorted(hashes)).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


__all__ = [
    "CONSTRAINED_VOLUME_URL",
    "EEA_EVENTS_URL",
    "HISTORICAL_SUPPLY_ADEQUACY_URL",
    "HISTORICAL_SUPPLY_CUSHION_URL",
    "HISTORICAL_TRANSMISSION_OUTAGES_URL",
    "OR_DIRECTIVES_URLS",
    "PLANNING_AREA_URL_TEMPLATE",
    "SYSTEM_FREQUENCY_URLS",
    "AesoResearchDataProvider",
]
