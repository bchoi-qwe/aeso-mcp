# SPDX-License-Identifier: MIT
"""Credential-free providers for the AESO forecast publications.

The URLs in this module are named AESO report products linked from the AESO
forecasting page.  They are deliberately constants: callers cannot supply a
URL, host, or path.  All requests use ``AesoPublicReportsHttpClient`` so an
APIM subscription key can never reach ETS or the public aeso.ca host.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from aeso_mcp.errors import DataValidationError, InvalidDateRangeError
from aeso_mcp.models.common import FinalityStatus, ObservationType, ProviderName
from aeso_mcp.models.forecasts import ForecastInterval
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient
from aeso_mcp.timeutil import (
    MARKET_TZ,
    add_elapsed,
    chronological_instant,
    in_half_open_range,
    parse_aeso_hour_ending,
    to_market,
    to_utc,
)

# These are the exact report targets linked by AESO's public forecasting page.
WIND_SOLAR_FORECAST_PAGE_URL = (
    "http://www.aeso.ca/grid/grid-planning/forecasting/wind-and-solar-power-forecasting/"
)
WIND_12_HOUR_URL = (
    "http://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/"
    "wind_solar_forecast/wind_rpt_shortterm.csv"
)
WIND_7_DAY_URL = (
    "http://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/"
    "wind_solar_forecast/wind_rpt_longterm.csv"
)
SOLAR_12_HOUR_URL = (
    "http://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/"
    "wind_solar_forecast/solar_rpt_shortterm.csv"
)
SOLAR_7_DAY_URL = (
    "http://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/"
    "wind_solar_forecast/solar_rpt_longterm.csv"
)
WIND_SOLAR_12_HOUR_URL = (
    "http://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/"
    "wind_solar_forecast/windsolar_rpt_shortterm.csv"
)
WIND_SOLAR_7_DAY_URL = (
    "http://ets.aeso.ca/Market/Reports/Manual/Operations/prodweb_reports/"
    "wind_solar_forecast/windsolar_rpt_longterm.csv"
)
POOL_PRICE_FORECAST_URL = (
    "http://ets.aeso.ca/ets_web/ip/Market/Reports/ActualForecastWMRQHReportServlet"
)

_CURRENT_URLS: dict[tuple[str, str], str] = {
    ("wind", "current_12_hour"): WIND_12_HOUR_URL,
    ("wind", "current_7_day"): WIND_7_DAY_URL,
    ("solar", "current_12_hour"): SOLAR_12_HOUR_URL,
    ("solar", "current_7_day"): SOLAR_7_DAY_URL,
    ("wind_solar", "current_12_hour"): WIND_SOLAR_12_HOUR_URL,
    ("wind_solar", "current_7_day"): WIND_SOLAR_7_DAY_URL,
}
_FORECAST_REQUIRED = frozenset(
    {"Forecast Transaction Date", "Min", "Most Likely", "Max", "Actual", "MCR"}
)
_POOL_REQUIRED = frozenset(
    {"Date", "Forecast Pool Price", "Actual Posted Pool Price", "Forecast AIL", "Actual AIL"}
)
_HISTORICAL_REQUIRED = frozenset(
    {"FORECAST_DATE_LOCAL", "FORECAST_DATE_GMT", "MAX", "MIN", "OPT", "MCR", "ACTUAL"}
)
_HISTORICAL_LINK_RE = re.compile(r"\b(Wind|Solar)\s+Data\s+(\d{4})\b", re.IGNORECASE)


def _provenance(product: str, url: str) -> dict[str, object]:
    return {
        "provider": ProviderName.AESO_PUBLIC_REPORT.value,
        "source_product": product,
        "source_url": url,
    }


class AesoForecastProvider:
    """Named, credential-free AESO forecast report adapter."""

    def __init__(self, http: AesoPublicReportsHttpClient) -> None:
        self._http = http

    async def get_current_forecast(
        self,
        series: LiteralForecastSeries,
        horizon: LiteralForecastHorizon,
    ) -> tuple[list[ForecastInterval], datetime | None, dict[str, object]]:
        """Fetch a current 12-hour or seven-day wind/solar publication."""
        url = _CURRENT_URLS.get((series, horizon))
        if url is None:
            raise InvalidDateRangeError(
                "Current forecast horizon is only supported for wind, solar, and wind_solar."
            )
        raw = await self._http.get_bytes(url)
        intervals = _parse_current_forecast_csv(raw, series=series, horizon=horizon)
        return (
            intervals,
            None,
            _provenance(f"{_series_name(series)} {horizon.replace('_', ' ')} forecast", url),
        )

    async def get_historical_forecast(
        self,
        series: LiteralRenewableSeries,
        start: datetime,
        end: datetime,
    ) -> tuple[list[ForecastInterval], datetime | None, dict[str, object]]:
        """Fetch official yearly wind/solar actual-vs-forecast files.

        AESO publishes one versioned file per calendar year.  The forecasting
        page is used only to resolve the current link for the requested year;
        the matched link text is checked and the HTTP client still enforces
        the public-host allow-list.
        """
        if to_utc(end) <= to_utc(start):
            raise InvalidDateRangeError("Historical forecast end must be after start.")
        start_m = to_market(start)
        end_m = to_market(end)
        final_instant = to_market(to_utc(end_m) - timedelta(microseconds=1))
        years = range(start_m.year, final_instant.year + 1)
        if len(years) > 3:
            raise InvalidDateRangeError(
                "Historical renewable forecast range cannot exceed 3 years."
            )
        page = await self._http.get_text(WIND_SOLAR_FORECAST_PAGE_URL)
        links = _historical_year_links(page)
        all_intervals: list[ForecastInterval] = []
        source_urls: list[str] = []
        for year in years:
            product = "Wind" if series == "wind" else "Solar"
            url = links.get((product.casefold(), year))
            if url is None:
                raise DataValidationError(
                    f"AESO forecasting page has no {product} actual-vs-forecast file for {year}."
                )
            raw = await self._http.get_bytes(url)
            all_intervals.extend(_parse_historical_forecast_csv(raw, series=series, year=year))
            source_urls.append(url)
        filtered = [
            item
            for item in all_intervals
            if in_half_open_range(item.interval_start, start_m, end_m)
        ]
        filtered.sort(key=lambda item: chronological_instant(item.interval_start))
        return (
            filtered,
            None,
            _provenance(
                f"{_series_name(series)} historical actual-vs-forecast", ";".join(source_urls)
            ),
        )

    async def get_pool_price_forecast(
        self,
        start_date: date,
        end_date: date,
    ) -> tuple[list[ForecastInterval], datetime | None, dict[str, object]]:
        """Fetch the official Forecast and Actual Pool Price report.

        The source also publishes AIL in the same report.  This method returns
        only Pool Price rows so price and load semantics cannot be conflated.
        """
        if end_date < start_date:
            raise InvalidDateRangeError("end_date must be on or after start_date.")
        if (end_date - start_date).days + 1 > 31:
            raise InvalidDateRangeError(
                "Pool-price forecast report requests cannot exceed 31 days."
            )
        url = (
            f"{POOL_PRICE_FORECAST_URL}?beginDate={start_date:%m%d%Y}"
            f"&endDate={end_date:%m%d%Y}&contentType=csv"
        )
        raw = await self._http.get_bytes(url)
        intervals, report_date = _parse_pool_price_forecast_csv(raw)
        intervals = [
            item
            for item in intervals
            if start_date <= to_market(item.interval_start).date() <= end_date
        ]
        return intervals, report_date, _provenance("Forecast and Actual Pool Price", url)


# Literal aliases keep the provider signatures narrow without importing the
# typing-only ``Literal`` object into public model APIs.
LiteralForecastSeries = Literal["wind", "solar", "wind_solar"]
LiteralForecastHorizon = Literal["current_12_hour", "current_7_day"]
LiteralRenewableSeries = Literal["wind", "solar"]


def _series_name(series: str) -> str:
    return {"wind": "Wind", "solar": "Solar", "wind_solar": "Wind and Solar"}.get(series, series)


def _parse_current_forecast_csv(
    raw: bytes,
    *,
    series: LiteralForecastSeries,
    horizon: LiteralForecastHorizon,
) -> list[ForecastInterval]:
    reader = _csv_reader(raw)
    _require_columns(reader.fieldnames, _FORECAST_REQUIRED, "AESO wind/solar forecast")
    rows: list[ForecastInterval] = []
    seen: set[datetime] = set()
    cadence = timedelta(minutes=10) if horizon == "current_12_hour" else timedelta(hours=1)
    for raw_row in reader:
        row = _clean_row(raw_row)
        label = _cell(row, "Forecast Transaction Date")
        if label is None:
            continue
        start = _parse_local_timestamp(label, "wind/solar forecast target")
        key = to_utc(start)
        if key in seen:
            raise DataValidationError(
                f"AESO wind/solar forecast contains duplicate target interval {label}."
            )
        seen.add(key)
        rows.append(
            ForecastInterval(
                interval_start=start,
                interval_end=add_elapsed(start, cadence),
                series=series,
                horizon=horizon,
                forecast_value=_optional_float(_cell(row, "Most Likely")),
                actual_value=_optional_float(_cell(row, "Actual")),
                minimum_value=_optional_float(_cell(row, "Min")),
                maximum_value=_optional_float(_cell(row, "Max")),
                capacity_mw=_optional_float(_cell(row, "MCR")),
                unit="MW",
                source_product=f"{_series_name(series)} {horizon.replace('_', ' ')} forecast",
                observation_type=ObservationType.FORECAST,
                finality=FinalityStatus.PRELIMINARY,
            )
        )
    if not rows:
        raise DataValidationError("AESO wind/solar forecast report was empty.")
    rows.sort(key=lambda item: chronological_instant(item.interval_start))
    return rows


def _parse_historical_forecast_csv(
    raw: bytes,
    *,
    series: LiteralRenewableSeries,
    year: int,
) -> list[ForecastInterval]:
    reader = _csv_reader(raw)
    _require_columns(reader.fieldnames, _HISTORICAL_REQUIRED, f"AESO {series} data {year}")
    rows: list[ForecastInterval] = []
    seen: set[datetime] = set()
    for raw_row in reader:
        row = _clean_row(raw_row)
        local_label = _cell(row, "FORECAST_DATE_LOCAL")
        gmt_label = _cell(row, "FORECAST_DATE_GMT")
        if local_label is None or gmt_label is None:
            continue
        start = _parse_historical_timestamp(local_label, gmt_label)
        key = to_utc(start)
        if key in seen:
            raise DataValidationError(
                f"AESO {series} historical file contains duplicate target interval {local_label}."
            )
        seen.add(key)
        rows.append(
            ForecastInterval(
                interval_start=start,
                interval_end=add_elapsed(start, timedelta(hours=1)),
                series=series,
                horizon="historical",
                forecast_value=_optional_float(_cell(row, "OPT")),
                actual_value=_optional_float(_cell(row, "ACTUAL")),
                minimum_value=_optional_float(_cell(row, "MIN")),
                maximum_value=_optional_float(_cell(row, "MAX")),
                capacity_mw=_optional_float(_cell(row, "MCR")),
                unit="MW",
                source_product=f"{_series_name(series)} historical actual-vs-forecast {year}",
                observation_type=ObservationType.FORECAST,
                finality=FinalityStatus.FINAL,
            )
        )
    if not rows:
        raise DataValidationError(f"AESO {series} historical data file {year} was empty.")
    rows.sort(key=lambda item: chronological_instant(item.interval_start))
    return rows


def _parse_pool_price_forecast_csv(
    raw: bytes,
) -> tuple[list[ForecastInterval], datetime | None]:
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.strip().startswith("Date,")), None
    )
    if header_index is None:
        raise DataValidationError("Forecast and Actual Pool Price report has no CSV header.")
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    _require_columns(reader.fieldnames, _POOL_REQUIRED, "Forecast and Actual Pool Price")
    report_date = _parse_report_date(text)
    rows: list[ForecastInterval] = []
    seen: set[datetime] = set()
    for raw_row in reader:
        row = _clean_row(raw_row)
        label = _cell(row, "Date")
        if label is None:
            continue
        try:
            start, end = parse_aeso_hour_ending(label)
        except ValueError as exc:
            raise DataValidationError(
                f"Forecast and Actual Pool Price has malformed Date value {label!r}."
            ) from exc
        key = to_utc(start)
        if key in seen:
            raise DataValidationError(
                f"Forecast and Actual Pool Price contains duplicate interval {label}."
            )
        seen.add(key)
        forecast = _optional_float(_cell(row, "Forecast Pool Price"))
        actual = _optional_float(_cell(row, "Actual Posted Pool Price"))
        if forecast is None and actual is None:
            # Future rows can legitimately have no posted actual, but a row
            # with neither value carries no observation and is not returned.
            continue
        rows.append(
            ForecastInterval(
                interval_start=start,
                interval_end=end,
                series="pool_price",
                horizon="historical",
                forecast_value=forecast,
                actual_value=actual,
                unit="CAD/MWh",
                source_product="Forecast and Actual Pool Price",
                observation_type=ObservationType.FORECAST,
                finality=FinalityStatus.PRELIMINARY,
            )
        )
    if not rows:
        raise DataValidationError(
            "Forecast and Actual Pool Price report contained no observations."
        )
    rows.sort(key=lambda item: chronological_instant(item.interval_start))
    return rows, report_date


def _historical_year_links(html: str) -> dict[tuple[str, int], str]:
    soup = BeautifulSoup(html, "html.parser")
    links: dict[tuple[str, int], str] = {}
    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(" ", strip=True)
        match = _HISTORICAL_LINK_RE.search(text)
        if match is None:
            continue
        product = match.group(1).casefold()
        year = int(match.group(2))
        href = str(anchor["href"]).strip()
        url = urljoin(WIND_SOLAR_FORECAST_PAGE_URL, href)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "www.aeso.ca",
            "aeso.ca",
        }:
            raise DataValidationError("AESO historical forecast link resolved outside aeso.ca.")
        key = (product, year)
        if key in links and links[key] != url:
            raise DataValidationError(
                f"AESO forecasting page has duplicate {product} {year} links."
            )
        links[key] = url
    return links


def _csv_reader(raw: bytes) -> csv.DictReader:
    return csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))


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
        return None
    text = value.strip()
    return text if text and text not in {"-", "—", "N/A", "n/a"} else None


def _optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace(",", "").replace("$", "")
    if not text or text in {"-", "—", "N/A", "n/a"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return float(text)
    except ValueError as exc:
        raise DataValidationError(
            f"AESO forecast report has malformed numeric value {value!r}."
        ) from exc


def _parse_local_timestamp(value: str, report: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=MARKET_TZ)
    except ValueError as exc:
        raise DataValidationError(f"{report} has malformed timestamp {value!r}.") from exc


def _parse_historical_timestamp(local_value: str, gmt_value: str) -> datetime:
    try:
        # The GMT column preserves chronology through a fall-back hour.  Keep
        # the returned value in the public market timezone.
        utc_value = datetime.strptime(gmt_value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        return utc_value.astimezone(MARKET_TZ)
    except ValueError:
        try:
            return datetime.fromisoformat(local_value.strip()).replace(tzinfo=MARKET_TZ)
        except ValueError as fallback_exc:
            raise DataValidationError(
                f"AESO historical forecast has malformed timestamp {local_value!r}/{gmt_value!r}."
            ) from fallback_exc


def _parse_report_date(text: str) -> datetime | None:
    match = re.search(r'"?([A-Za-z]+\s+\d{1,2},\s+\d{4})\.?"?', text)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").replace(tzinfo=MARKET_TZ)
    except ValueError as exc:
        raise DataValidationError(
            "Forecast and Actual Pool Price has malformed report date."
        ) from exc
