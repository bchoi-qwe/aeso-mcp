# SPDX-License-Identifier: MIT
"""AESO public-report provider (credential-free, allow-listed hosts only)."""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup

from aeso_mcp.errors import DataValidationError
from aeso_mcp.models.common import ProviderName
from aeso_mcp.models.market_power import McsinrInterval, SecondaryOfferPriceLimitInterval
from aeso_mcp.models.transmission import TransmissionOutageRecord
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient
from aeso_mcp.timeutil import MARKET_TZ, to_market

logger = logging.getLogger(__name__)

LONG_RANGE_LANDING_URL = "http://ets.aeso.ca/outage_reports/Longterm_Critical_Outages.html"
MCSINR_CSV_URL = "http://ets.aeso.ca/ets_web/ip/Market/Reports/MCSINRReportServlet?contentType=csv"
SOC_CSV_URL = "http://ets.aeso.ca/ets_web/ip/Market/Reports/CurrentSOCReportServlet?contentType=csv"
_PUBLISH_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})")
_REPORT_TIME_RE = re.compile(r"Report Time:\s*(.+?)\"?\s*$", re.IGNORECASE | re.MULTILINE)
_HE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2})$")


def _provenance(product: str) -> dict[str, str]:
    return {
        "provider": ProviderName.AESO_PUBLIC_REPORT.value,
        "source_product": product,
    }


class AesoPublicReportsProvider:
    """Named public reports only — no generic URL fetch surface."""

    def __init__(self, http: AesoPublicReportsHttpClient) -> None:
        self._http = http

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
        return intervals, report_time, _provenance("Secondary Offer Price Limit")


def _publication_time_from_href(href: str) -> datetime | None:
    match = _PUBLISH_RE.search(href.replace("\\", "/"))
    if not match:
        return None
    stamp = f"{match.group(1)} {match.group(2).replace('-', ':')}"
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MARKET_TZ)


def _parse_long_range_csv(
    raw: bytes,
    *,
    publication_time: datetime | None,
) -> list[TransmissionOutageRecord]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return []
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


def _parse_mcsinr_csv(raw: bytes) -> tuple[list[McsinrInterval], datetime | None]:
    text = raw.decode("utf-8-sig", errors="replace")
    report_time = _parse_report_time(text)
    table = _csv_table_after_headers(text)
    reader = csv.DictReader(io.StringIO(table))
    if reader.fieldnames is None:
        return [], report_time
    field_map = {name: name.strip() for name in reader.fieldnames if name}
    intervals: list[McsinrInterval] = []
    for raw_row in reader:
        row = {field_map.get(k, k): v for k, v in raw_row.items()}
        label = _cell(row, "Date (HE)")
        bounds = _parse_hour_ending(label)
        if bounds is None:
            continue
        start, end = bounds
        intervals.append(
            McsinrInterval(
                interval_start=start,
                interval_end=end,
                hour_ending_label=label or "",
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
    if reader.fieldnames is None:
        return [], report_time
    field_map = {name: name.strip() for name in reader.fieldnames if name}
    intervals: list[SecondaryOfferPriceLimitInterval] = []
    for raw_row in reader:
        row = {field_map.get(k, k): v for k, v in raw_row.items()}
        begin_label = _first_cell(row, ("Effective Begin (HE)",))
        end_label = _first_cell(row, ("Effective End (HE)",))
        begin = _parse_hour_ending(begin_label)
        end = _parse_hour_ending(end_label)
        intervals.append(
            SecondaryOfferPriceLimitInterval(
                effective_begin=begin[0] if begin else None,
                effective_end=end[1] if end else None,
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


def _parse_hour_ending(label: str | None) -> tuple[datetime, datetime] | None:
    if not label:
        return None
    match = _HE_RE.match(label.strip())
    if not match:
        return None
    month, day, year, hour = (int(match.group(i)) for i in range(1, 5))
    if hour < 1 or hour > 24:
        return None
    if hour == 24:
        end = datetime(year, month, day, 0, 0, tzinfo=MARKET_TZ) + timedelta(days=1)
    else:
        end = datetime(year, month, day, hour, 0, tzinfo=MARKET_TZ)
    return end - timedelta(hours=1), end


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
    if value is None:
        return None
    text = value.strip().replace(",", "").replace("$", "")
    if not text or text in {"-", "—", "n/a", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


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
