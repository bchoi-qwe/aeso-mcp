# SPDX-License-Identifier: MIT
"""Source-shape contracts for official AESO forecasts and operational reports."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.errors import DataValidationError
from aeso_mcp.providers.forecasts import (
    AesoForecastProvider,
    _parse_current_forecast_csv,
    _parse_historical_forecast_csv,
    _parse_pool_price_forecast_csv,
)
from aeso_mcp.providers.reports import (
    _parse_dds_csv,
    _parse_ffr_net_schedule_csv,
    _parse_supply_adequacy_html,
    _parse_supply_surplus_html,
    _parse_system_events_csv,
    _parse_tmr_csv,
)
from aeso_mcp.services.reports import _supply_surplus_events
from aeso_mcp.timeutil import MARKET_TZ, chronological_instant

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_current_forecast_accepts_extra_columns_and_preserves_missing_actual() -> None:
    rows = _parse_current_forecast_csv(
        _fixture("forecast_current.csv"),
        series="wind",
        horizon="current_12_hour",
    )

    assert len(rows) == 2
    assert rows[0].forecast_value == 125.0
    assert rows[0].actual_value == 121.0
    assert rows[1].actual_value is None
    assert (rows[1].interval_start - rows[0].interval_start).total_seconds() == 600


def test_forecast_schema_drift_and_duplicate_targets_fail_loudly() -> None:
    with pytest.raises(DataValidationError, match="missing required column"):
        _parse_current_forecast_csv(
            b"Forecast Transaction Date,Min,Renamed,Max,Actual,MCR\n",
            series="solar",
            horizon="current_7_day",
        )

    duplicate = (
        b"Forecast Transaction Date,Min,Most Likely,Max,Actual,MCR\n"
        b"2026-08-26 12:00,1,2,3,2,4\n"
        b"2026-08-26 12:00,1,2,3,2,4\n"
    )
    with pytest.raises(DataValidationError, match="duplicate target"):
        _parse_current_forecast_csv(
            duplicate,
            series="solar",
            horizon="current_12_hour",
        )


def test_historical_forecast_uses_gmt_to_distinguish_fall_back_hours() -> None:
    rows = _parse_historical_forecast_csv(
        _fixture("forecast_historical_dst.csv"),
        series="wind",
        year=2024,
    )

    assert len(rows) == 2
    assert chronological_instant(rows[1].interval_start) > chronological_instant(
        rows[0].interval_start
    )
    assert rows[0].interval_start.utcoffset() != rows[1].interval_start.utcoffset()


@pytest.mark.asyncio
async def test_historical_forecast_half_open_year_boundary_does_not_fetch_next_year() -> None:
    http = AsyncMock()
    http.get_text.return_value = (
        '<a href="https://www.aeso.ca/files/wind-2024.csv">Wind Data 2024</a>'
    )
    http.get_bytes.return_value = _fixture("forecast_historical_dst.csv")
    provider = AesoForecastProvider(http)

    rows, _, _ = await provider.get_historical_forecast(
        "wind",
        datetime(2024, 11, 3, tzinfo=MARKET_TZ),
        datetime(2025, 1, 1, tzinfo=MARKET_TZ),
    )

    assert len(rows) == 2
    http.get_bytes.assert_awaited_once_with("https://www.aeso.ca/files/wind-2024.csv")


def test_pool_price_forecast_keeps_forecast_distinct_and_parses_accounting_negative() -> None:
    rows, publication_time = _parse_pool_price_forecast_csv(_fixture("pool_price_forecast.csv"))

    assert publication_time is not None
    assert publication_time.date().isoformat() == "2026-08-26"
    assert rows[0].forecast_value == 42.5
    assert rows[0].actual_value == 40.0
    assert all(row.publication_time is None for row in rows)
    assert rows[1].forecast_value == -15.25
    assert rows[1].actual_value is None


def test_supply_adequacy_preserves_official_status_bands_without_inventing_mw() -> None:
    rows = _parse_supply_adequacy_html(_fixture("supply_adequacy.html"))

    assert len(rows) == 24
    assert rows[0].adequacy_status_code == 4
    assert rows[0].supply_cushion_code == 8
    assert rows[0].forecast_demand_mw is None
    assert rows[0].supply_cushion_mw is None


def test_supply_adequacy_distinguishes_repeated_fall_back_hour() -> None:
    hours = ["1", "2", "2X", *[str(value) for value in range(3, 25)]]
    header = "".join(f"<th>{value}</th>" for value in hours)
    adequacy = "".join("<td>4</td>" for _ in hours)
    cushion = "".join("<td>8</td>" for _ in hours)
    raw = (
        "<html><body>"
        f"<table><tr><th>HE</th>{header}</tr>"
        f"<tr><td>11/03/2024</td>{adequacy}</tr></table>"
        f"<table><tr><th>HE</th>{header}</tr>"
        f"<tr><td>11/03/2024</td>{cushion}</tr></table>"
        "</body></html>"
    ).encode()

    rows = _parse_supply_adequacy_html(raw)

    assert len(rows) == 25
    repeated = [row for row in rows if row.interval_start.hour == 1]
    assert len(repeated) == 2
    assert repeated[0].interval_start.utcoffset() != repeated[1].interval_start.utcoffset()


def test_supply_surplus_events_end_only_at_explicit_non_surplus_boundary() -> None:
    rows = _parse_supply_surplus_html(_fixture("supply_surplus.html"))
    events = _supply_surplus_events(rows)

    assert len(events) == 1
    assert events[0].status == "all_zero_forecast_prices"
    assert events[0].end == rows[2].interval_start


def test_ffr_parser_preserves_import_sign_and_fall_back_chronology() -> None:
    rows = _parse_ffr_net_schedule_csv(_fixture("ffr_net_schedule.csv"))

    assert len(rows) == 2
    assert rows[0].net_schedule_mw == -125.5
    assert rows[1].net_schedule_mw == -75.0
    assert chronological_instant(rows[1].interval_start) > chronological_instant(
        rows[0].interval_start
    )


def test_dds_tmr_and_system_event_reports_keep_source_semantics() -> None:
    dds = _parse_dds_csv(_fixture("dds_market.csv"))
    tmr = _parse_tmr_csv(_fixture("tmr_reference.csv"))
    events = _parse_system_events_csv(_fixture("system_events.csv"))

    assert [row.available_dds_mw for row in dds] == [125.5, 0.0]
    assert tmr[1].reference_price_cad_per_mwh == -5.25
    assert [event.event_type for event in events] == ["supply_surplus", "energy_emergency"]


@pytest.mark.parametrize(
    ("parser", "payload", "message"),
    [
        (_parse_dds_csv, b"Date/Time,Renamed\n08/26/2026 12:00,1\n", "missing required"),
        (
            _parse_system_events_csv,
            b"Date/Time,Comments\nnot-a-time,Outage\n",
            "malformed timestamp",
        ),
        (
            _parse_tmr_csv,
            b"Date,Price($)\n08/01/2026,42\n08/01/2026,43\n",
            "duplicate date",
        ),
    ],
)
def test_report_schema_timestamp_and_duplicate_failures(
    parser, payload: bytes, message: str
) -> None:
    with pytest.raises(DataValidationError, match=message):
        parser(payload)
