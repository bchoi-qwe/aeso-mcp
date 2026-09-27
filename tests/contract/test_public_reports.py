# SPDX-License-Identifier: MIT
"""Contract tests for AESO public-report provider (no live ETS in CI)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from aeso_mcp.config import Settings
from aeso_mcp.errors import DataValidationError, RateLimitError, UpstreamUnavailableError
from aeso_mcp.providers.public_reports import (
    LONG_RANGE_LANDING_URL,
    AesoPublicReportsProvider,
)
from aeso_mcp.providers.public_reports_http import AesoPublicReportsHttpClient

FIXTURES = Path(__file__).parent / "fixtures"


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"aeso_api_key": "test-key", **overrides})


@pytest.fixture
async def public_provider():
    client = AesoPublicReportsHttpClient(_settings())
    try:
        yield AesoPublicReportsProvider(client)
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_long_range_outages_html_to_csv(public_provider: AesoPublicReportsProvider) -> None:
    html = (FIXTURES / "long_range_outages.html").read_text(encoding="utf-8")
    csv_body = (FIXTURES / "long_range_outages.csv").read_bytes()
    respx.get(LONG_RANGE_LANDING_URL).mock(return_value=httpx.Response(200, text=html))
    respx.get(url__regex=r".*csvData/.*Longterm_Critical_outages\.csv").mock(
        return_value=httpx.Response(200, content=csv_body)
    )
    outages, publication_time, meta = await public_provider.get_long_range_transmission_outages()
    assert meta["provider"] == "aeso_public_report"
    assert publication_time is not None
    assert publication_time.year == 2026
    assert len(outages) == 2
    assert outages[0].approval_status == "tentative"
    assert outages[0].element.startswith("876s")
    assert outages[0].interval_start.tzinfo is not None


@respx.mock
@pytest.mark.asyncio
async def test_long_range_missing_csv_link(public_provider: AesoPublicReportsProvider) -> None:
    respx.get(LONG_RANGE_LANDING_URL).mock(
        return_value=httpx.Response(200, text="<html><body>No CSV</body></html>")
    )
    with pytest.raises(DataValidationError, match="CSV"):
        await public_provider.get_long_range_transmission_outages()


@respx.mock
@pytest.mark.asyncio
async def test_mcsinr_and_soc_csv_parsing(public_provider: AesoPublicReportsProvider) -> None:
    mcsinr = (FIXTURES / "mcsinr_current.csv").read_bytes()
    soc = (FIXTURES / "soc_current.csv").read_bytes()
    respx.get(url__regex=r".*MCSINRReportServlet.*").mock(
        return_value=httpx.Response(200, content=mcsinr, headers={"Content-Type": "text/csv"})
    )
    respx.get(url__regex=r".*CurrentSOCReportServlet.*").mock(
        return_value=httpx.Response(200, content=soc, headers={"Content-Type": "text/csv"})
    )

    intervals, report_time, meta = await public_provider.get_monthly_cumulative_net_revenue()
    assert meta["provider"] == "aeso_public_report"
    assert report_time is not None
    assert len(intervals) >= 2
    # First data row after incomplete HE may be null; find a populated row.
    populated = [i for i in intervals if i.cumulative_net_revenue_cad is not None]
    assert populated
    assert populated[0].secondary_offer_price_limit_triggered is False
    assert populated[0].interval_start.tzinfo is not None
    negative = next(i for i in intervals if i.hour_ending_label.strip().endswith("20"))
    assert negative.cumulative_net_revenue_cad == pytest.approx(-46931.24)

    soc_rows, soc_time, soc_meta = await public_provider.get_secondary_offer_price_limit()
    assert soc_meta["provider"] == "aeso_public_report"
    assert soc_time is not None
    assert len(soc_rows) == 1
    assert soc_rows[0].limit_in_effect is False
    assert soc_rows[0].secondary_offer_price_limit_cad_per_mwh is None


@pytest.mark.asyncio
async def test_public_client_rejects_foreign_host() -> None:
    client = AesoPublicReportsHttpClient(_settings())
    try:
        with pytest.raises(DataValidationError, match="allow-listed"):
            await client.get_text("https://example.com/report.csv")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_public_client_rejects_api_key_in_url() -> None:
    client = AesoPublicReportsHttpClient(_settings())
    try:
        with pytest.raises(DataValidationError, match="Credentials"):
            await client.get_text("http://ets.aeso.ca/outage_reports/x.csv?API-KEY=secret")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_public_client_rejects_userinfo_in_url() -> None:
    client = AesoPublicReportsHttpClient(_settings())
    try:
        with pytest.raises(DataValidationError, match="Credentials"):
            await client.get_text("http://user:pass@ets.aeso.ca/outage_reports/x.csv")
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_public_client_rejects_cross_host_redirect() -> None:
    respx.get("http://ets.aeso.ca/report").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    client = AesoPublicReportsHttpClient(_settings())
    try:
        with pytest.raises(DataValidationError, match="allow-listed"):
            await client.get_text("http://ets.aeso.ca/report")
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_mcsinr_schema_drift_fails_loudly(
    public_provider: AesoPublicReportsProvider,
) -> None:
    bad = b'Monthly\n"Report Time: Friday, August 07 2026 06:48:39 PM"\nDate (HE),Wrong Column\n'
    respx.get(url__regex=r".*MCSINRReportServlet.*").mock(
        return_value=httpx.Response(200, content=bad)
    )
    with pytest.raises(DataValidationError, match="schema changed"):
        await public_provider.get_monthly_cumulative_net_revenue()


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [(404, DataValidationError), (429, RateLimitError), (503, UpstreamUnavailableError)],
)
async def test_public_client_maps_http_failures(status: int, error_type: type[Exception]) -> None:
    respx.get("http://ets.aeso.ca/report.csv").mock(return_value=httpx.Response(status))
    client = AesoPublicReportsHttpClient(_settings(http_max_retries=0))
    try:
        with pytest.raises(error_type):
            await client.get_bytes("http://ets.aeso.ca/report.csv")
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_public_client_stops_reading_when_stream_exceeds_body_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChunkedBody(httpx.AsyncByteStream):
        fully_consumed = False

        async def __aiter__(self):
            yield b"123"
            yield b"456"
            self.fully_consumed = True
            yield b"789"

        async def aclose(self) -> None:
            pass

    stream = ChunkedBody()
    monkeypatch.setattr("aeso_mcp.providers.public_reports_http._MAX_PUBLIC_REPORT_BYTES", 4)
    respx.get("http://ets.aeso.ca/report.csv").mock(return_value=httpx.Response(200, stream=stream))
    client = AesoPublicReportsHttpClient(_settings())
    try:
        with pytest.raises(DataValidationError, match="4 bytes safety limit"):
            await client.get_bytes("http://ets.aeso.ca/report.csv")
        assert not stream.fully_consumed
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_public_client_rejects_unexpected_binary_content() -> None:
    respx.get("http://ets.aeso.ca/report.csv").mock(
        return_value=httpx.Response(200, content=b"PK\x00binary")
    )
    client = AesoPublicReportsHttpClient(_settings())
    try:
        with pytest.raises(DataValidationError, match="binary content"):
            await client.get_bytes("http://ets.aeso.ca/report.csv")
    finally:
        await client.aclose()
