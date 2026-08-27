# SPDX-License-Identifier: MIT
"""Credential-free providers for named AESO operational reports."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup

from aeso_mcp.errors import DataValidationError, InvalidDateRangeError
from aeso_mcp.models.common import ProviderName
from aeso_mcp.models.reports import (
    DdsAvailabilityRecord,
    FfrNetScheduleInterval,
    SupplyAdequacyInterval,
    SupplySurplusInterval,
    SupplySurplusStatus,
    SystemEvent,
    SystemEventType,
    TmrReferencePrice,
)
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient
from aeso_mcp.timeutil import MARKET_TZ, add_elapsed, chronological_instant, parse_aeso_hour_ending

SUPPLY_SURPLUS_URL = (
    "http://ets.aeso.ca/ets_web/ip/Market/Reports/SupplySurplusReportServlet?contentType=html"
)
SUPPLY_ADEQUACY_URL = "http://ets.aeso.ca/ets_web/ip/Market/Reports/SupplyAdequacyReportServlet"
FFR_NET_SCHEDULE_URL = (
    "http://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/"
    "Intertie_capability_historical/intertie_capacity_historical.csv"
)
DDS_MARKET_REPORT_URL = (
    "http://ets.aeso.ca/ets_web/ip/Market/Reports/DDSMarketReportServlet?contentType=csv"
)
TMR_REFERENCE_PRICE_URL = (
    "http://ets.aeso.ca/ets_web/ip/Market/Reports/TMRPriceReportServlet?contentType=csv"
)
AIES_EVENT_LOG_URL = (
    "http://ets.aeso.ca/ets_web/ip/Market/Reports/RealTimeShiftReportServlet?contentType=csv"
)

_FFR_REQUIRED = frozenset(
    {"Date", "(HE)", "FFR Net Schedule (MW)", "BC Intertie (MW)", "MATL Intertie (MW)"}
)
_DDS_REQUIRED = frozenset({"Date/Time", "Available DDS (MW)"})
_TMR_REQUIRED = frozenset({"Date", "Price($)"})
_EVENT_REQUIRED = frozenset({"Date/Time", "Comments"})

_ADEQUACY_STATUS = {
    0: "unable_to_maintain_3_percent_reserves",
    1: "unable_to_maintain_6_percent_reserves",
    2: "0_to_200_mw_supply_available",
    3: "200_to_400_mw_supply_available",
    4: "greater_than_400_mw_supply_available",
}
_CUSHION_STATUS = {
    0: "0_mw_or_less",
    1: "above_0_to_200_mw",
    2: "above_200_to_400_mw",
    3: "above_400_to_600_mw",
    4: "above_600_to_800_mw",
    5: "above_800_to_1000_mw",
    6: "above_1000_to_2000_mw",
    7: "above_2000_to_3000_mw",
    8: "above_3000_mw",
}
_SURPLUS_STATUS: dict[int, SupplySurplusStatus] = {
    0: "all_zero_forecast_prices",
    1: "some_zero_forecast_prices",
    2: "all_forecast_prices_positive",
}


def _provenance(product: str, url: str) -> dict[str, object]:
    return {
        "provider": ProviderName.AESO_PUBLIC_REPORT.value,
        "source_product": product,
        "source_url": url,
    }


class AesoReportsProvider:
    """Named AESO report adapter; no arbitrary URL or report selection surface."""

    def __init__(self, http: AesoPublicReportsHttpClient) -> None:
        self._http = http

    async def get_supply_adequacy(
        self,
    ) -> tuple[list[SupplyAdequacyInterval], datetime | None, dict[str, object]]:
        raw = await self._http.get_bytes(SUPPLY_ADEQUACY_URL)
        intervals = _parse_supply_adequacy_html(raw)
        return (
            intervals,
            None,
            _provenance("Supply Adequacy and Market Supply Cushion", SUPPLY_ADEQUACY_URL),
        )

    async def get_supply_surplus(
        self,
    ) -> tuple[list[SupplySurplusInterval], datetime | None, dict[str, object]]:
        raw = await self._http.get_bytes(SUPPLY_SURPLUS_URL)
        intervals = _parse_supply_surplus_html(raw)
        return intervals, None, _provenance("Supply Surplus", SUPPLY_SURPLUS_URL)

    async def get_ffr_net_schedule(
        self,
    ) -> tuple[list[FfrNetScheduleInterval], datetime | None, dict[str, object]]:
        raw = await self._http.get_bytes(FFR_NET_SCHEDULE_URL)
        intervals = _parse_ffr_net_schedule_csv(raw)
        return intervals, None, _provenance("FFR Net Schedule", FFR_NET_SCHEDULE_URL)

    async def get_dds_market_report(
        self,
        start_date: date,
        end_date: date,
    ) -> tuple[list[DdsAvailabilityRecord], datetime | None, dict[str, object]]:
        if end_date < start_date:
            raise InvalidDateRangeError("end_date must be on or after start_date.")
        url = (
            f"http://ets.aeso.ca/ets_web/ip/Market/Reports/DDSMarketReportServlet"
            f"?beginDate={start_date:%m%d%Y}&endDate={end_date:%m%d%Y}&contentType=csv"
        )
        raw = await self._http.get_bytes(url)
        records = _parse_dds_csv(raw)
        return records, None, _provenance("Dispatch Down Service Market Report", url)

    async def get_tmr_reference_price(
        self,
    ) -> tuple[list[TmrReferencePrice], datetime | None, dict[str, object]]:
        raw = await self._http.get_bytes(TMR_REFERENCE_PRICE_URL)
        records = _parse_tmr_csv(raw)
        return records, None, _provenance("TMR Reference Price", TMR_REFERENCE_PRICE_URL)

    async def get_system_events(
        self,
        start_date: date,
        end_date: date,
    ) -> tuple[list[SystemEvent], datetime | None, dict[str, object]]:
        if end_date < start_date:
            raise InvalidDateRangeError("end_date must be on or after start_date.")
        url = (
            f"http://ets.aeso.ca/ets_web/ip/Market/Reports/RealTimeShiftReportServlet"
            f"?beginDate={start_date:%m%d%Y}&endDate={end_date:%m%d%Y}&contentType=csv"
        )
        raw = await self._http.get_bytes(url)
        records = _parse_system_events_csv(raw)
        return records, None, _provenance("AIES Event Log", url)


def _parse_supply_adequacy_html(raw: bytes) -> list[SupplyAdequacyInterval]:
    soup = BeautifulSoup(raw.decode("utf-8-sig", errors="replace"), "html.parser")
    grids = _find_hourly_grids(soup)
    if len(grids) < 2:
        raise DataValidationError("Supply Adequacy report is missing one or more hourly grids.")
    adequacy = _parse_hourly_grid(grids[0], allowed=_ADEQUACY_STATUS, report="Supply Adequacy")
    cushion = _parse_hourly_grid(grids[1], allowed=_CUSHION_STATUS, report="Market Supply Cushion")
    by_key: dict[datetime, SupplyAdequacyInterval] = {}
    for start, code in adequacy:
        key = chronological_instant(start)
        by_key[key] = SupplyAdequacyInterval(
            interval_start=start,
            interval_end=add_elapsed(start, _ONE_HOUR),
            adequacy_status_code=code,
            adequacy_status=_ADEQUACY_STATUS[code],
        )
    for start, code in cushion:
        key = chronological_instant(start)
        current = by_key.get(key)
        if current is None:
            current = SupplyAdequacyInterval(
                interval_start=start,
                interval_end=add_elapsed(start, _ONE_HOUR),
            )
        by_key[key] = current.model_copy(
            update={"supply_cushion_code": code, "supply_cushion_status": _CUSHION_STATUS[code]}
        )
    if not by_key:
        raise DataValidationError("Supply Adequacy report contained no hourly observations.")
    return [by_key[key] for key in sorted(by_key)]


def _parse_supply_surplus_html(raw: bytes) -> list[SupplySurplusInterval]:
    soup = BeautifulSoup(raw.decode("utf-8-sig", errors="replace"), "html.parser")
    table = next(
        (
            table
            for table in soup.find_all("table")
            if [
                cell.get_text(" ", strip=True)
                for cell in table.find_all("tr")[0].find_all(["td", "th"])
            ]
            == ["Date (HE)", "Status"]
        ),
        None,
    )
    if table is None:
        raise DataValidationError("Supply Surplus report is missing its Date (HE)/Status table.")
    intervals: list[SupplySurplusInterval] = []
    seen: set[datetime] = set()
    for row in table.find_all("tr")[1:]:
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        if not cells:
            continue
        if len(cells) != 2:
            raise DataValidationError("Supply Surplus report row has an unexpected column count.")
        start, _ = _parse_report_hour(cells[0], "Supply Surplus")
        code = _integer(cells[1], "Supply Surplus status")
        if code not in _SURPLUS_STATUS:
            raise DataValidationError(f"Supply Surplus report has unknown status code {code}.")
        key = chronological_instant(start)
        if key in seen:
            raise DataValidationError(f"Supply Surplus report has duplicate interval {cells[0]!r}.")
        seen.add(key)
        intervals.append(
            SupplySurplusInterval(
                interval_start=start,
                interval_end=add_elapsed(start, _ONE_HOUR),
                status_code=code,
                status=_SURPLUS_STATUS[code],
            )
        )
    if not intervals:
        raise DataValidationError("Supply Surplus report contained no observations.")
    intervals.sort(key=lambda item: chronological_instant(item.interval_start))
    return intervals


def _parse_ffr_net_schedule_csv(raw: bytes) -> list[FfrNetScheduleInterval]:
    reader = _csv_reader_after_header(raw, "Date,")
    _require_columns(reader.fieldnames, _FFR_REQUIRED, "FFR Net Schedule")
    rows: list[FfrNetScheduleInterval] = []
    seen: set[datetime] = set()
    for raw_row in reader:
        row = _clean_row(raw_row)
        day = _cell(row, "Date")
        hour = _cell(row, "(HE)")
        if day is None or hour is None:
            continue
        try:
            # The FFR archive labels the repeated fall-back HE 02 as ``02X``;
            # the shared AESO parser uses its explicit ``02*`` spelling.
            normalized_hour = f"{hour[:-1]}*" if hour.upper().endswith("X") else str(int(hour))
            start, end = parse_aeso_hour_ending(f"{day} {normalized_hour}")
        except (TypeError, ValueError) as exc:
            raise DataValidationError(
                f"FFR Net Schedule has malformed Date/(HE): {day} {hour}."
            ) from exc
        key = chronological_instant(start)
        if key in seen:
            raise DataValidationError(f"FFR Net Schedule has duplicate interval {day} {hour}.")
        seen.add(key)
        rows.append(
            FfrNetScheduleInterval(
                interval_start=start,
                interval_end=end,
                net_schedule_mw=_required_float(
                    _cell(row, "FFR Net Schedule (MW)"), "FFR net schedule"
                ),
                bc_intertie_mw=_optional_float(_cell(row, "BC Intertie (MW)"), "BC intertie"),
                matl_intertie_mw=_optional_float(_cell(row, "MATL Intertie (MW)"), "MATL intertie"),
                sask_intertie_mw=_optional_float(_cell(row, " SASK Intertie (MW)"), "SASK intertie")
                if " SASK Intertie (MW)" in row
                else _optional_float(_cell(row, "SASK Intertie (MW)"), "SASK intertie"),
            )
        )
    if not rows:
        raise DataValidationError("FFR Net Schedule report contained no observations.")
    rows.sort(key=lambda item: chronological_instant(item.interval_start))
    return rows


def _parse_dds_csv(raw: bytes) -> list[DdsAvailabilityRecord]:
    reader = _csv_reader_after_header(raw, "Date/Time,")
    _require_columns(reader.fieldnames, _DDS_REQUIRED, "DDS Market Report")
    rows: list[DdsAvailabilityRecord] = []
    seen: set[datetime] = set()
    for raw_row in reader:
        row = _clean_row(raw_row)
        timestamp = _cell(row, "Date/Time")
        if timestamp is None:
            continue
        observed_at = _parse_datetime(timestamp, "DDS timestamp")
        if observed_at in seen:
            raise DataValidationError(f"DDS Market Report has duplicate timestamp {timestamp!r}.")
        seen.add(observed_at)
        rows.append(
            DdsAvailabilityRecord(
                observed_at=observed_at,
                available_dds_mw=_required_float(_cell(row, "Available DDS (MW)"), "available DDS"),
            )
        )
    rows.sort(key=lambda item: chronological_instant(item.observed_at))
    return rows


def _parse_tmr_csv(raw: bytes) -> list[TmrReferencePrice]:
    reader = _csv_reader_after_header(raw, "Date,Price")
    _require_columns(reader.fieldnames, _TMR_REQUIRED, "TMR Reference Price")
    rows: list[TmrReferencePrice] = []
    seen: set[date] = set()
    for raw_row in reader:
        row = _clean_row(raw_row)
        date_value = _cell(row, "Date")
        if date_value is None:
            continue
        effective_date = _parse_date(date_value, "TMR effective date")
        if effective_date in seen:
            raise DataValidationError(f"TMR Reference Price has duplicate date {date_value!r}.")
        seen.add(effective_date)
        rows.append(
            TmrReferencePrice(
                effective_date=effective_date,
                reference_price_cad_per_mwh=_required_float(
                    _cell(row, "Price($)"), "TMR reference price"
                ),
            )
        )
    if not rows:
        raise DataValidationError("TMR Reference Price report contained no observations.")
    rows.sort(key=lambda item: item.effective_date)
    return rows


def _parse_system_events_csv(raw: bytes) -> list[SystemEvent]:
    reader = _csv_reader_after_header(raw, "Date/Time,Comments")
    _require_columns(reader.fieldnames, _EVENT_REQUIRED, "AIES Event Log")
    rows: list[SystemEvent] = []
    seen: set[tuple[datetime, str]] = set()
    for raw_row in reader:
        row = _clean_row(raw_row)
        timestamp = _cell(row, "Date/Time")
        comments = _cell(row, "Comments")
        if timestamp is None and comments is None:
            continue
        if timestamp is None or comments is None:
            raise DataValidationError("AIES Event Log has a row missing Date/Time or Comments.")
        event_time = _parse_datetime(timestamp, "AIES Event Log timestamp")
        key = (event_time, comments)
        if key in seen:
            raise DataValidationError(f"AIES Event Log has duplicate event {timestamp!r}.")
        seen.add(key)
        rows.append(
            SystemEvent(
                event_time=event_time,
                comments=comments,
                event_type=_classify_event(comments),
            )
        )
    rows.sort(key=lambda item: chronological_instant(item.event_time))
    return rows


def _find_hourly_grids(soup: BeautifulSoup) -> list[Any]:
    grids: list[Any] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        cells = [cell.get_text(" ", strip=True) for cell in rows[0].find_all(["td", "th"])]
        if cells and cells[0].casefold() == "he" and _valid_hour_headers(cells[1:]):
            grids.append(table)
    return grids


def _parse_hourly_grid(
    table: Any,
    *,
    allowed: dict[int, str],
    report: str,
) -> list[tuple[datetime, int]]:
    values: dict[datetime, tuple[datetime, int]] = {}
    header_cells = [
        cell.get_text(" ", strip=True) for cell in table.find_all("tr")[0].find_all(["td", "th"])
    ]
    hour_labels = header_cells[1:]
    for row in table.find_all("tr")[1:]:
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        if not cells:
            continue
        if len(cells) != len(hour_labels) + 1:
            # The legacy ETS markup places the colour-code legend inside the
            # same outer table.  It is not an observation row; a row that
            # starts with a date but has the wrong width is still schema drift.
            if not cells[0].strip():
                continue
            try:
                _parse_date(cells[0].split()[0], f"{report} date")
            except DataValidationError:
                continue
            raise DataValidationError(f"{report} row has an unexpected column count.")
        day_label = cells[0]
        for hour_label, raw_code in zip(hour_labels, cells[1:], strict=True):
            start, _ = _parse_report_hour(day_label, report, hour=hour_label)
            code = _integer(raw_code, f"{report} code")
            if code not in allowed:
                raise DataValidationError(f"{report} has unknown status code {code}.")
            key = chronological_instant(start)
            if key in values:
                raise DataValidationError(
                    f"{report} contains duplicate interval {day_label} HE {hour_label}."
                )
            values[key] = (start, code)
    return [values[key] for key in sorted(values)]


def _parse_report_hour(
    value: str,
    report: str,
    *,
    hour: int | str | None = None,
) -> tuple[datetime, datetime]:
    day_text = value.strip().split()[0]
    parsed = _parse_date(day_text, f"{report} date")
    label = f"{parsed:%m/%d/%Y} {hour}" if hour is not None else value
    if label.upper().endswith("X"):
        label = f"{label[:-1]}*"
    try:
        return parse_aeso_hour_ending(label)
    except ValueError as exc:
        raise DataValidationError(f"{report} has malformed hour-ending {label!r}.") from exc


def _valid_hour_headers(values: Sequence[str]) -> bool:
    """Accept regular, spring-forward, and fall-back AESO hourly grids."""
    normalized = tuple(value.strip().upper() for value in values)
    regular = [str(value) for value in range(1, 25)]
    spring = [value for value in regular if value != "2"]
    fall = ["1", "2", "2X", *[str(value) for value in range(3, 25)]]
    return normalized in {tuple(regular), tuple(spring), tuple(fall)}


def _csv_reader_after_header(raw: bytes, prefix: str) -> csv.DictReader:
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    index = next(
        (position for position, line in enumerate(lines) if line.strip().startswith(prefix)), None
    )
    if index is None:
        raise DataValidationError("AESO public report did not contain its expected CSV header.")
    return csv.DictReader(io.StringIO("\n".join(lines[index:])), skipinitialspace=True)


def _require_columns(
    fieldnames: Sequence[str] | None,
    required: frozenset[str],
    report: str,
) -> None:
    if fieldnames is None:
        raise DataValidationError(f"{report} CSV has no header row.")
    normalized = {name.strip() for name in fieldnames if name}
    missing = sorted(required - normalized)
    if missing:
        raise DataValidationError(
            f"{report} CSV schema changed; missing required column(s): {', '.join(missing)}."
        )


def _clean_row(row: dict[str | None, str | None]) -> dict[str, str | None]:
    return {
        str(key).strip(): value.strip() if isinstance(value, str) else value
        for key, value in row.items()
        if key is not None
    }


def _cell(row: dict[str, str | None], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        # Some source headers contain a leading blank (for example the SASK
        # intertie column); compare stripped names without relaxing required
        # column checks.
        for candidate, item in row.items():
            if candidate.strip() == key.strip():
                value = item
                break
    if value is None:
        return None
    text = value.strip()
    return text if text and text not in {"-", "—", "N/A", "n/a"} else None


def _optional_float(value: str | None, label: str) -> float | None:
    if value is None:
        return None
    try:
        return _number(value)
    except ValueError as exc:
        raise DataValidationError(f"{label} has malformed numeric value {value!r}.") from exc


def _required_float(value: str | None, label: str) -> float:
    parsed = _optional_float(value, label)
    if parsed is None:
        raise DataValidationError(f"{label} is missing from an AESO report row.")
    return parsed


def _number(value: str) -> float:
    text = value.strip().replace(",", "").replace("$", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    return float(text)


def _integer(value: str, label: str) -> int:
    match = re.search(r"-?\d+", value)
    if match is None:
        raise DataValidationError(f"{label} has malformed integer value {value!r}.")
    return int(match.group(0))


def _parse_datetime(value: str, label: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y %H:%M").replace(tzinfo=MARKET_TZ)
    except ValueError as exc:
        raise DataValidationError(f"{label} has malformed timestamp {value!r}.") from exc


def _parse_date(value: str, label: str) -> date:
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise DataValidationError(f"{label} has malformed date {value!r}.")


def _classify_event(comments: str) -> SystemEventType:
    text = comments.casefold()
    if "supply surplus" in text:
        return "supply_surplus"
    if "energy emergency" in text or "emergency alert" in text:
        return "energy_emergency"
    if "maintenance" in text:
        return "maintenance"
    if any(term in text for term in ("offline", "out of service", "outage")):
        return "outage"
    if "market" in text or "restatement" in text:
        return "market_operation"
    return "other"


_ONE_HOUR = timedelta(hours=1)
