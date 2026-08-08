# SPDX-License-Identifier: MIT
"""AESO public-report provider (credential-free, allow-listed hosts only)."""

from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from bs4 import BeautifulSoup

from aeso_mcp.errors import DataValidationError, InvalidDateRangeError
from aeso_mcp.models.common import ProviderName
from aeso_mcp.models.market_power import McsinrInterval, SecondaryOfferPriceLimitInterval
from aeso_mcp.models.transmission import TransmissionOutageRecord
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient
from aeso_mcp.timeutil import MARKET_TZ, parse_aeso_hour_ending, to_market

logger = logging.getLogger(__name__)

APPROVED_TX_LANDING_URL = "http://ets.aeso.ca/outage_reports/qryOpPlanTransmissionTable_1.html"
LONG_RANGE_LANDING_URL = "http://ets.aeso.ca/outage_reports/Longterm_Critical_Outages.html"
MCSINR_CSV_URL = "http://ets.aeso.ca/ets_web/ip/Market/Reports/MCSINRReportServlet?contentType=csv"
SOC_CSV_URL = "http://ets.aeso.ca/ets_web/ip/Market/Reports/CurrentSOCReportServlet?contentType=csv"

_PUBLISH_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})")
_REPORT_TIME_RE = re.compile(r"Report Time:\s*(.+?)\"?\s*$", re.IGNORECASE | re.MULTILINE)
_EARLIEST_APPROVED_TX = date(2024, 1, 31)
_ARCHIVE_JUMP_URL = (
    "http://ets.aeso.ca/outage_reports/archives/"
    "_2025-01-17_14-10-25_qryOpPlanTransmissionTable_1.html"
)
_MAX_NAVIGATION_ATTEMPTS = 500

_LONG_RANGE_REQUIRED = frozenset({"Element", "From"})
_APPROVED_TX_REQUIRED = frozenset({"Element", "From", "To", "Owner", "Type"})
_MCSINR_REQUIRED = frozenset(
    {
        "Date (HE)",
        "Monthly Cumulative Settlement Interval Net Revenue ($)",
        "1/6 Annualized Unavoidable Costs ($)",
        "Secondary Offer Price Limit Triggered",
    }
)
_SOC_REQUIRED = frozenset(
    {
        "Effective Begin (HE)",
        "Effective End (HE)",
        "Secondary Offer Price Limit in Effect",
        "Secondary Offer Price Limit ($)",
    }
)


def _provenance(product: str) -> dict[str, str]:
    return {
        "provider": ProviderName.AESO_PUBLIC_REPORT.value,
        "source_product": product,
    }


class AesoPublicReportsProvider:
    """Named public reports only — no generic URL fetch surface."""

    def __init__(self, http: AesoPublicReportsHttpClient) -> None:
        self._http = http

    async def get_approved_transmission_outages(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[TransmissionOutageRecord], datetime | None, dict[str, str]]:
        """Fetch AESO-approved planned transmission outages from ETS HTML→CSV."""
        if start is None and end is None:
            records, publication_time = await self._fetch_approved_latest()
        else:
            if start is None or end is None:
                raise InvalidDateRangeError(
                    "Provide both start and end for historical approved transmission outages."
                )
            records, publication_time = await self._fetch_approved_historical(
                to_market(start), to_market(end)
            )
        return (
            records,
            publication_time,
            _provenance("Approved Transmission Outages (ETS public report)"),
        )

    async def get_long_range_transmission_outages(
        self,
    ) -> tuple[list[TransmissionOutageRecord], datetime | None, dict[str, str]]:
        """Fetch Long Range Significant Transmission Outages (tentative)."""
        html = await self._http.get_text(LONG_RANGE_LANDING_URL)
        soup = BeautifulSoup(html, "html.parser")
        csv_link = soup.find("a", href=lambda x: isinstance(x, str) and "csvData" in x)
        if csv_link is None or not csv_link.get("href"):
            raise DataValidationError(
                "Long Range Significant Transmission Outages page has no CSV download link."
            )
        href = str(csv_link["href"])
        csv_url = self._http.resolve_outage_report_url(href, base=LONG_RANGE_LANDING_URL)
        publication_time = _publication_time_from_href(href)
        raw = await self._http.get_bytes(csv_url)
        records = _parse_long_range_csv(raw, publication_time=publication_time)
        return records, publication_time, _provenance("Long Range Significant Transmission Outages")

    async def get_monthly_cumulative_net_revenue(
        self,
    ) -> tuple[list[McsinrInterval], datetime | None, dict[str, str]]:
        """Fetch current Monthly Cumulative Settlement Interval Net Revenue CSV."""
        raw = await self._http.get_bytes(MCSINR_CSV_URL)
        intervals, report_time = _parse_mcsinr_csv(raw)
        intervals.sort(key=lambda i: i.interval_start, reverse=True)
        return (
            intervals,
            report_time,
            _provenance("Monthly Cumulative Settlement Interval Net Revenue"),
        )

    async def get_secondary_offer_price_limit(
        self,
    ) -> tuple[list[SecondaryOfferPriceLimitInterval], datetime | None, dict[str, str]]:
        """Fetch current Secondary Offer Price Limit CSV."""
        raw = await self._http.get_bytes(SOC_CSV_URL)
        intervals, report_time = _parse_soc_csv(raw)
        intervals.sort(
            key=lambda i: (
                i.public_notification_time
                or i.effective_begin
                or datetime.min.replace(tzinfo=MARKET_TZ)
            ),
            reverse=True,
        )
        return intervals, report_time, _provenance("Secondary Offer Price Limit")

    async def _fetch_approved_latest(
        self,
    ) -> tuple[list[TransmissionOutageRecord], datetime | None]:
        html = await self._http.get_text(APPROVED_TX_LANDING_URL)
        href, publication_time = _csv_href_and_publish_time(html)
        csv_url = self._http.resolve_outage_report_url(href, base=APPROVED_TX_LANDING_URL)
        raw = await self._http.get_bytes(csv_url)
        return _parse_approved_tx_csv(raw, publication_time=publication_time), publication_time

    async def _fetch_approved_historical(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[TransmissionOutageRecord], datetime | None]:
        start_d = start.date()
        end_d = end.date()
        if end_d < _EARLIEST_APPROVED_TX:
            raise DataValidationError(
                "Approved transmission outage archives are only available from "
                f"{_EARLIEST_APPROVED_TX.isoformat()} onwards."
            )
        if start_d < _EARLIEST_APPROVED_TX:
            logger.warning(
                "approved_tx_history_clamped requested_start=%s earliest=%s",
                start_d.isoformat(),
                _EARLIEST_APPROVED_TX.isoformat(),
            )
            start = datetime(
                _EARLIEST_APPROVED_TX.year,
                _EARLIEST_APPROVED_TX.month,
                _EARLIEST_APPROVED_TX.day,
                tzinfo=MARKET_TZ,
            )

        current_url = APPROVED_TX_LANDING_URL
        historical: list[tuple[str, datetime]] = []
        jumped_to_archives = False

        for _ in range(_MAX_NAVIGATION_ATTEMPTS):
            html = await self._http.get_text(current_url)
            soup = BeautifulSoup(html, "html.parser")
            csv_link = soup.find("a", href=lambda x: isinstance(x, str) and "csvData" in x)
            if csv_link is not None and csv_link.get("href"):
                href = str(csv_link["href"])
                publication_time = _publication_time_from_href(href)
                if publication_time is not None:
                    # Match service-layer half-open [start, end) semantics.
                    if start <= publication_time < end:
                        csv_url = self._http.resolve_outage_report_url(href, base=current_url)
                        historical.append((csv_url, publication_time))
                    if publication_time < start:
                        break
                    if publication_time.date() <= date(2025, 1, 22) and not jumped_to_archives:
                        current_url = _ARCHIVE_JUMP_URL
                        jumped_to_archives = True
                        continue

            prev_link = soup.find(
                "a",
                string=lambda text: isinstance(text, str) and "Previous Version" in text,
            )
            if prev_link is None or not prev_link.get("href"):
                break
            current_url = self._http.resolve_outage_report_url(
                str(prev_link["href"]), base=current_url
            )

        if not historical:
            raise DataValidationError(
                "No approved transmission outage publications found in the requested window."
            )

        all_records: list[TransmissionOutageRecord] = []
        latest_pub: datetime | None = None
        for csv_url, publication_time in historical:
            raw = await self._http.get_bytes(csv_url)
            all_records.extend(_parse_approved_tx_csv(raw, publication_time=publication_time))
            if latest_pub is None or publication_time > latest_pub:
                latest_pub = publication_time
        all_records.sort(
            key=lambda r: r.publication_time or datetime.min.replace(tzinfo=MARKET_TZ),
            reverse=True,
        )
        return all_records, latest_pub


def _csv_href_and_publish_time(html: str) -> tuple[str, datetime | None]:
    soup = BeautifulSoup(html, "html.parser")
    csv_link = soup.find("a", href=lambda x: isinstance(x, str) and "csvData" in x)
    if csv_link is None or not csv_link.get("href"):
        raise DataValidationError("Approved Transmission Outages page has no CSV download link.")
    href = str(csv_link["href"])
    return href, _publication_time_from_href(href)


def _publication_time_from_href(href: str) -> datetime | None:
    match = _PUBLISH_RE.search(href.replace("\\", "/"))
    if not match:
        return None
    stamp = f"{match.group(1)} {match.group(2).replace('-', ':')}"
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MARKET_TZ)


def _require_columns(
    fieldnames: Sequence[str] | None, required: frozenset[str], report: str
) -> None:
    if fieldnames is None:
        raise DataValidationError(f"{report} CSV has no header row.")
    normalized = {name.strip() for name in fieldnames if name}
    missing = sorted(col for col in required if col not in normalized)
    if missing:
        raise DataValidationError(
            f"{report} CSV schema changed; missing required column(s): {', '.join(missing)}."
        )


def _parse_long_range_csv(
    raw: bytes,
    *,
    publication_time: datetime | None,
) -> list[TransmissionOutageRecord]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    _require_columns(reader.fieldnames, _LONG_RANGE_REQUIRED, "Long Range Transmission Outages")
    records: list[TransmissionOutageRecord] = []
    for row in reader:
        element = _cell(row, "Element")
        if not element:
            continue
        start = _parse_aeso_outage_dt(_cell(row, "From"))
        if start is None:
            continue
        end = _parse_aeso_outage_dt(_cell(row, "To"))
        records.append(
            TransmissionOutageRecord(
                interval_start=to_market(start),
                interval_end=to_market(end) if end is not None else None,
                publication_time=publication_time,
                transmission_owner=_cell(row, "Owner"),
                element_type=None,
                element=element,
                scheduled_activity=_cell(row, "Scheduled Activity"),
                comments=_cell(row, "Duration"),
                interconnection=_cell(row, "Affected Intertie") or _cell(row, "Interconnection"),
                approval_status="tentative",
                duration_note=_cell(row, "Duration"),
            )
        )
    return records


def _parse_approved_tx_csv(
    raw: bytes,
    *,
    publication_time: datetime | None,
) -> list[TransmissionOutageRecord]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    _require_columns(reader.fieldnames, _APPROVED_TX_REQUIRED, "Approved Transmission Outages")
    records: list[TransmissionOutageRecord] = []
    for row in reader:
        element = _cell(row, "Element")
        if not element:
            continue
        start = _parse_aeso_outage_dt(_cell(row, "From"))
        if start is None:
            continue
        end = _parse_aeso_outage_dt(_cell(row, "To"))
        records.append(
            TransmissionOutageRecord(
                interval_start=to_market(start),
                interval_end=to_market(end) if end is not None else None,
                publication_time=publication_time,
                transmission_owner=_cell(row, "Owner"),
                element_type=_cell(row, "Type"),
                element=element,
                scheduled_activity=_cell(row, "Scheduled Activity"),
                comments=_cell(row, "Date/Time Comments") or _cell(row, "Date Time Comments"),
                interconnection=_cell(row, "Interconnection"),
                approval_status="approved",
                duration_note=_cell(row, "Date/Time Comments") or _cell(row, "Date Time Comments"),
            )
        )
    return records


def _parse_mcsinr_csv(raw: bytes) -> tuple[list[McsinrInterval], datetime | None]:
    text = raw.decode("utf-8-sig", errors="replace")
    report_time = _parse_report_time(text)
    table = _csv_table_after_headers(text)
    reader = csv.DictReader(io.StringIO(table))
    _require_columns(reader.fieldnames, _MCSINR_REQUIRED, "MCSINR")
    field_map = {name: name.strip() for name in reader.fieldnames or [] if name}
    intervals: list[McsinrInterval] = []
    for raw_row in reader:
        row = {field_map.get(k, k): v for k, v in raw_row.items()}
        label = _cell(row, "Date (HE)")
        if not label:
            continue
        try:
            start, end = parse_aeso_hour_ending(label)
        except ValueError:
            logger.warning("unparseable_mcsinr_hour_ending value=%s", label[:40])
            continue
        intervals.append(
            McsinrInterval(
                interval_start=start,
                interval_end=end,
                hour_ending_label=label,
                cumulative_net_revenue_cad=_parse_optional_float(
                    _cell(row, "Monthly Cumulative Settlement Interval Net Revenue ($)")
                ),
                one_sixth_annualized_unavoidable_costs_cad=_parse_optional_float(
                    _cell(row, "1/6 Annualized Unavoidable Costs ($)")
                ),
                secondary_offer_price_limit_triggered=_parse_optional_bool(
                    _cell(row, "Secondary Offer Price Limit Triggered")
                ),
            )
        )
    return intervals, report_time


def _parse_soc_csv(
    raw: bytes,
) -> tuple[list[SecondaryOfferPriceLimitInterval], datetime | None]:
    text = raw.decode("utf-8-sig", errors="replace")
    report_time = _parse_report_time(text)
    table = _csv_table_after_headers(text)
    reader = csv.DictReader(io.StringIO(table))
    _require_columns(reader.fieldnames, _SOC_REQUIRED, "Secondary Offer Price Limit")
    field_map = {name: name.strip() for name in reader.fieldnames or [] if name}
    intervals: list[SecondaryOfferPriceLimitInterval] = []
    for raw_row in reader:
        row = {field_map.get(k, k): v for k, v in raw_row.items()}
        begin_label = _first_cell(row, ("Effective Begin (HE)",))
        end_label = _first_cell(row, ("Effective End (HE)",))
        begin = None
        end = None
        if begin_label:
            try:
                begin = parse_aeso_hour_ending(begin_label)[0]
            except ValueError:
                logger.warning("unparseable_soc_begin value=%s", begin_label[:40])
        if end_label:
            try:
                end = parse_aeso_hour_ending(end_label)[1]
            except ValueError:
                logger.warning("unparseable_soc_end value=%s", end_label[:40])
        intervals.append(
            SecondaryOfferPriceLimitInterval(
                effective_begin=begin,
                effective_end=end,
                begin_label=begin_label,
                end_label=end_label,
                limit_in_effect=_parse_optional_bool(
                    _first_cell(row, ("Secondary Offer Price Limit in Effect",))
                ),
                secondary_offer_price_limit_cad_per_mwh=_parse_optional_float(
                    _first_cell(row, ("Secondary Offer Price Limit ($)",))
                ),
                public_notification_time=_parse_notification_time(
                    _first_cell(row, ("Public Notification Time",))
                ),
            )
        )
    return intervals, report_time


def _csv_table_after_headers(text: str) -> str:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if "Date (HE)" in line or "Effective Begin" in line:
            return "\n".join(lines[idx:])
    return text


def _parse_report_time(text: str) -> datetime | None:
    match = _REPORT_TIME_RE.search(text)
    if not match:
        return None
    stamp = match.group(1).strip().strip('"')
    for fmt in ("%A, %B %d %Y %I:%M:%S %p", "%A, %B %d %Y %H:%M:%S"):
        try:
            return datetime.strptime(stamp, fmt).replace(tzinfo=MARKET_TZ)
        except ValueError:
            continue
    logger.warning("unparseable_report_time value=%s", stamp[:60])
    return None


def _parse_aeso_outage_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in ("%d-%b-%y %H:%M", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=MARKET_TZ)
        except ValueError:
            continue
    logger.warning("unparseable_transmission_outage_timestamp value=%s", text[:40])
    return None


def _parse_notification_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=MARKET_TZ)
        except ValueError:
            continue
    return None


def _parse_optional_float(value: str | None) -> float | None:
    """Parse floats including accounting negatives like ``(46931.24)``."""
    if value is None:
        return None
    text = value.strip().replace(",", "").replace("$", "").replace(" ", "")
    if not text or text in {"-", "—", "n/a", "N/A"}:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    text = value.strip().lower()
    if not text or text in {"-", "—", "n/a"}:
        return None
    if text in {"yes", "y", "true", "1"}:
        return True
    if text in {"no", "n", "false", "0"}:
        return False
    return None


def _cell(row: dict[str, Any], key: str) -> str | None:
    raw = row.get(key)
    if raw is None:
        for candidate, value in row.items():
            if isinstance(candidate, str) and candidate.strip() == key:
                raw = value
                break
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == "\xa0":
        return None
    return text


def _first_cell(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _cell(row, key)
        if value is not None:
            return value
    for candidate, value in row.items():
        if not isinstance(candidate, str):
            continue
        stripped = candidate.strip()
        if any(key in stripped for key in keys):
            text = str(value).strip()
            if text and text != "\xa0":
                return text
    return None
