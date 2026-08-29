# SPDX-License-Identifier: MIT
"""Navigation and boundary coverage for the approved ETS report provider."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from aeso_mcp.errors import DataValidationError, InvalidDateRangeError
from aeso_mcp.providers.public_reports import AesoPublicReportsProvider
from aeso_mcp.timeutil import MARKET_TZ


def _approved_csv() -> bytes:
    return (
        b"Owner,From,To,Type,Element,Date/Time Comments,Interconnection\n"
        b"ATCO,01-Aug-26 08:00,02-Aug-26 08:00,Outage,826s 504R,note,BC\n"
    )


def _page(publication: str, previous: str | None = None) -> str:
    previous_link = f'<a href="{previous}">Previous Version</a>' if previous else ""
    return (
        "<html><body>"
        f'<a href="csvData\\_{publication}_qryOpPlanTransmissionTable_1.csv">CSV</a>'
        f"{previous_link}"
        "</body></html>"
    )


@pytest.mark.asyncio
async def test_approved_historical_navigation_clamps_and_sorts_publications() -> None:
    http = AsyncMock()
    http.get_text.side_effect = [
        _page("2026-08-05_15-11-00", "/previous.html"),
        _page("2026-08-03_14-10-00"),
    ]
    http.get_bytes.side_effect = [_approved_csv(), _approved_csv()]
    http.resolve_outage_report_url = MagicMock(
        side_effect=lambda href, *, base: (
            f"http://ets.aeso.ca{href}" if href.startswith("/") else href
        )
    )
    provider = AesoPublicReportsProvider(http)

    records, publication_time, metadata = await provider.get_approved_transmission_outages(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2026, 8, 7, tzinfo=UTC),
    )

    assert len(records) == 2
    assert publication_time == datetime(2026, 8, 5, 15, 11, tzinfo=MARKET_TZ)
    assert records[0].publication_time == publication_time
    assert records[0].approval_status == "approved"
    assert metadata["source_product"].startswith("Approved")
    assert http.get_text.await_count == 2
    assert http.get_bytes.await_count == 2


@pytest.mark.asyncio
async def test_approved_historical_navigation_rejects_unavailable_windows() -> None:
    http = AsyncMock()
    provider = AesoPublicReportsProvider(http)

    with pytest.raises(DataValidationError, match="only available"):
        await provider.get_approved_transmission_outages(
            start=datetime(2023, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 1, tzinfo=UTC),
        )

    http.get_text.return_value = _page("2026-08-05_15-11-00")
    http.resolve_outage_report_url = MagicMock(side_effect=lambda href, *, base: href)
    with pytest.raises(DataValidationError, match="No approved"):
        await provider.get_approved_transmission_outages(
            start=datetime(2026, 9, 1, tzinfo=UTC),
            end=datetime(2026, 9, 2, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_approved_provider_requires_complete_historical_bounds() -> None:
    provider = AesoPublicReportsProvider(AsyncMock())

    with pytest.raises(InvalidDateRangeError, match="both start and end"):
        await provider.get_approved_transmission_outages(start=datetime(2026, 8, 1, tzinfo=UTC))
