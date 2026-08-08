# SPDX-License-Identifier: MIT
"""Contract tests for direct AESO APIM provider using fixtures + respx."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

from aeso_mcp.config import Settings
from aeso_mcp.errors import (
    AuthenticationError,
    DataValidationError,
    RateLimitError,
    UpstreamUnavailableError,
)
from aeso_mcp.providers.aeso_apim import AesoApimProvider
from aeso_mcp.providers.http import AesoHttpClient
from aeso_mcp.timeutil import MARKET_TZ

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://apimgw.aeso.ca/public"


def _settings() -> Settings:
    return Settings(aeso_api_key="test-key")  # type: ignore[arg-type]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
async def provider():
    client = AesoHttpClient(_settings())
    try:
        yield AesoApimProvider(client)
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_pool_price_valid_response(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*poolPrice.*").mock(
        return_value=httpx.Response(200, json=_load("pool_price_ok.json"))
    )
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    intervals, meta = await provider.get_pool_prices(start, end)
    assert len(intervals) == 2
    assert intervals[0].pool_price_cad_per_mwh == 45.67
    assert intervals[0].interval_start.tzinfo is not None
    assert meta["provider"] == "aeso_apim"


@respx.mock
@pytest.mark.asyncio
async def test_pool_price_empty_response(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*poolPrice.*").mock(
        return_value=httpx.Response(200, json={"return": {"Pool Price Report": []}})
    )
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    intervals, _ = await provider.get_pool_prices(start, end)
    assert intervals == []


@respx.mock
@pytest.mark.asyncio
async def test_auth_401(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*poolPrice.*").mock(return_value=httpx.Response(401, json={}))
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(AuthenticationError):
        await provider.get_pool_prices(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_auth_403(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*poolPrice.*").mock(return_value=httpx.Response(403, json={}))
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(AuthenticationError):
        await provider.get_pool_prices(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_404(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*poolPrice.*").mock(return_value=httpx.Response(404, json={}))
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(DataValidationError):
        await provider.get_pool_prices(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_429(provider: AesoApimProvider) -> None:
    # Disable retries for this assertion by patching settings max retries to 0
    provider._http._settings.http_max_retries = 0  # type: ignore[attr-defined]
    respx.get(url__regex=r".*poolPrice.*").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "2"}, json={})
    )
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(RateLimitError):
        await provider.get_pool_prices(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_500(provider: AesoApimProvider) -> None:
    provider._http._settings.http_max_retries = 0  # type: ignore[attr-defined]
    respx.get(url__regex=r".*poolPrice.*").mock(return_value=httpx.Response(500, json={}))
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(UpstreamUnavailableError):
        await provider.get_pool_prices(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_malformed_json(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*poolPrice.*").mock(
        return_value=httpx.Response(200, text="not-json", headers={"Content-Type": "text/plain"})
    )
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(DataValidationError, match="malformed JSON"):
        await provider.get_pool_prices(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_missing_expected_fields_skipped(provider: AesoApimProvider) -> None:
    payload = {
        "return": {
            "Pool Price Report": [
                {"begin_datetime_utc": "2024-01-15T07:00:00.000Z"},  # missing price
                {
                    "begin_datetime_utc": "2024-01-15T08:00:00.000Z",
                    "pool_price": 10.0,
                    "unexpected_extra_field": "ok",
                },
            ]
        }
    }
    respx.get(url__regex=r".*poolPrice.*").mock(return_value=httpx.Response(200, json=payload))
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    intervals, _ = await provider.get_pool_prices(start, end)
    assert len(intervals) == 1
    assert intervals[0].pool_price_cad_per_mwh == 10.0


@respx.mock
@pytest.mark.asyncio
async def test_smp_and_csd(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*systemMarginalPrice.*").mock(
        return_value=httpx.Response(200, json=_load("smp_ok.json"))
    )
    respx.get(url__regex=r".*csd/summary/current.*").mock(
        return_value=httpx.Response(200, json=_load("csd_ok.json"))
    )
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    smp, _ = await provider.get_system_marginal_prices(start, end)
    assert smp[0].system_marginal_price_cad_per_mwh == 41.5
    assert smp[1].system_marginal_price_cad_per_mwh == 999.99

    observed, components, _ = await provider.get_fuel_mix()
    assert observed.tzinfo is not None
    assert any(c.fuel_type == "Wind" for c in components)

    _, paths, net, _ = await provider.get_interchange()
    assert net == pytest.approx(160.0)
    assert len(paths) == 3


@respx.mock
@pytest.mark.asyncio
async def test_timeout(provider: AesoApimProvider) -> None:
    provider._http._settings.http_max_retries = 0  # type: ignore[attr-defined]
    respx.get(url__regex=r".*poolPrice.*").mock(side_effect=httpx.ReadTimeout("slow"))
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(UpstreamUnavailableError, match="timed out"):
        await provider.get_pool_prices(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_connection_failure(provider: AesoApimProvider) -> None:
    provider._http._settings.http_max_retries = 0  # type: ignore[attr-defined]
    respx.get(url__regex=r".*poolPrice.*").mock(side_effect=httpx.ConnectError("nope"))
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(UpstreamUnavailableError, match="connect"):
        await provider.get_pool_prices(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_api_key_not_in_exception_message(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*poolPrice.*").mock(return_value=httpx.Response(401, json={}))
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(AuthenticationError) as exc:
        await provider.get_pool_prices(start, end)
    assert "test-key" not in str(exc.value)


@pytest.mark.asyncio
async def test_apim_stubs_raise_unsupported(provider: AesoApimProvider) -> None:
    from aeso_mcp.errors import UnsupportedDatasetError

    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(UnsupportedDatasetError, match="Historical generation"):
        await provider.get_generation_history(start, end)
    with pytest.raises(UnsupportedDatasetError, match="outages"):
        await provider.get_outages(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_apim_rejects_cross_host_redirect(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*poolPrice.*").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    with pytest.raises(DataValidationError, match="allow-listed"):
        await provider.get_pool_prices(start, end)


@respx.mock
@pytest.mark.asyncio
async def test_apim_follows_same_host_redirect(provider: AesoApimProvider) -> None:
    respx.get(url__regex=r".*poolPrice.*").mock(
        side_effect=[
            httpx.Response(
                302,
                headers={
                    "Location": (
                        "https://apimgw.aeso.ca/public/poolprice-api/v1.1/price/poolPrice?ok=1"
                    )
                },
            ),
            httpx.Response(200, json=_load("pool_price_ok.json")),
        ]
    )
    start = datetime(2024, 1, 15, tzinfo=MARKET_TZ)
    end = datetime(2024, 1, 16, tzinfo=MARKET_TZ)
    intervals, _ = await provider.get_pool_prices(start, end)
    assert len(intervals) == 2
