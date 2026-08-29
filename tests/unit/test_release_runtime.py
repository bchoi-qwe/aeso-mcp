# SPDX-License-Identifier: MIT
"""Behavioral coverage for release-critical public-report runtime paths."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from aeso_mcp.config import Settings
from aeso_mcp.errors import (
    DataValidationError,
    InvalidDateRangeError,
    RateLimitError,
    UpstreamUnavailableError,
)
from aeso_mcp.models.common import (
    DataCompleteness,
    DataStatus,
    FinalityStatus,
    ObservationType,
    ProviderName,
)
from aeso_mcp.models.market_power import (
    MarketPowerMitigationRequest,
    McsinrInterval,
    SecondaryOfferPriceLimitInterval,
)
from aeso_mcp.models.reserves import (
    OperatingReserveActivationInterval,
    OperatingReserveDateRangeRequest,
    OperatingReserveForecastInterval,
    OperatingReserveForecastRequest,
    OperatingReservePriceInterval,
    OperatingReserveSummaryRequest,
)
from aeso_mcp.providers.archive_http import AesoArchiveHttpClient
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.market_power import MarketPowerService
from aeso_mcp.services.reserves import OperatingReserveService
from aeso_mcp.timeutil import MARKET_TZ


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"aeso_api_key": "test-key", **overrides})


def _price(
    market_date: date,
    procurement: str,
    reserve_type: str,
    block: str,
    *,
    active: float | None = None,
    blended: float | None = None,
    volume: float | None = None,
) -> OperatingReservePriceInterval:
    return OperatingReservePriceInterval(
        market_date=market_date,
        procurement=procurement,
        reserve_type=reserve_type,
        time_block=block,
        active_price_cad_per_mw=active,
        clearing_blended_price_cad_per_mw=blended,
        volume_mw=volume,
    )


@pytest.mark.asyncio
async def test_operating_reserve_prices_filter_page_and_cache() -> None:
    market_date = date(2026, 8, 1)
    provider = AsyncMock()
    provider.get_operating_reserve_prices.return_value = (
        [
            _price(market_date, "active", "regulating", "on_peak", active=8, volume=90),
            _price(market_date, "active", "spinning", "on_peak", active=12, volume=100),
            _price(market_date, "standby", "spinning", "off_peak", blended=18, volume=110),
        ],
        datetime(2026, 8, 2, 15, tzinfo=UTC),
        {"provider": "aeso_public_report", "source_product": "Active/Standby OR"},
    )
    service = OperatingReserveService(provider, _settings(), AsyncTTLCache())
    request = OperatingReserveDateRangeRequest(
        start_date=market_date,
        end_date=market_date,
        reserve_types=["spinning"],
        offset=1,
        limit=1,
    )

    response = await service.get_prices(request)
    assert len(response.intervals) == 1
    assert response.intervals[0].procurement == "standby"
    assert response.page.offset == 1
    assert response.page.returned == 1
    assert response.page.total == 2
    assert response.page.next_offset is None
    assert response.metadata.observation_count == 2
    assert response.metadata.completeness == DataCompleteness.COMPLETE
    assert response.metadata.source_product == "Active/Standby OR"
    assert response.metadata.request_start == datetime(2026, 8, 1, tzinfo=MARKET_TZ)
    assert response.metadata.request_end == datetime(2026, 8, 2, tzinfo=MARKET_TZ)
    assert response.metadata.cache_hit is False

    cached = await service.get_prices(request)
    assert cached.metadata.cache_hit is True
    assert provider.get_operating_reserve_prices.await_count == 1


@pytest.mark.asyncio
async def test_operating_reserve_empty_result_and_range_bounds() -> None:
    market_date = date(2026, 8, 1)
    provider = AsyncMock()
    provider.get_operating_reserve_prices.return_value = ([], None, {})
    service = OperatingReserveService(provider, _settings(), AsyncTTLCache())
    request = OperatingReserveDateRangeRequest(
        start_date=market_date,
        end_date=market_date,
        limit=10,
    )

    response = await service.get_prices(request)
    assert response.intervals == []
    assert response.page.returned == 0
    assert response.page.total == 0
    assert response.page.limit == 10
    assert response.metadata.completeness == DataCompleteness.EMPTY
    assert response.metadata.available_series == []

    reversed_request = OperatingReserveDateRangeRequest.model_construct(
        start_date=market_date,
        end_date=market_date - timedelta(days=1),
        reserve_types=[],
        offset=0,
        limit=500,
    )
    with pytest.raises(InvalidDateRangeError, match="on or after"):
        await service.get_prices(reversed_request)

    long_request = OperatingReserveDateRangeRequest.model_construct(
        start_date=market_date,
        end_date=market_date + timedelta(days=366),
        reserve_types=[],
        offset=0,
        limit=500,
    )
    with pytest.raises(InvalidDateRangeError, match="cannot exceed 366"):
        await service.get_prices(long_request)


@pytest.mark.asyncio
async def test_operating_reserve_forecast_filter_preserves_selected_series() -> None:
    interval_start = datetime(2026, 8, 1, 0, tzinfo=MARKET_TZ)
    forecast = OperatingReserveForecastInterval(
        interval_start=interval_start,
        interval_end=interval_start + timedelta(hours=1),
        active_regulating_mw=100,
        active_spinning_mw=200,
        active_supplemental_mw=300,
        standby_regulating_mw=40,
        standby_spinning_mw=50,
        standby_supplemental_mw=60,
    )
    provider = AsyncMock()
    provider.get_operating_reserve_forecast.return_value = (
        [forecast],
        datetime(2026, 8, 1, 1, tzinfo=MARKET_TZ),
        {"source_product": "Seven-day Forecast"},
    )
    service = OperatingReserveService(provider, _settings(), AsyncTTLCache())

    filtered = await service.get_forecast(
        OperatingReserveForecastRequest(reserve_types=["regulating"], limit=1)
    )
    selected = filtered.intervals[0]
    assert selected.active_regulating_mw == 100
    assert selected.standby_regulating_mw == 40
    assert selected.active_spinning_mw is None
    assert selected.active_supplemental_mw is None
    assert selected.standby_spinning_mw is None
    assert selected.standby_supplemental_mw is None
    assert filtered.metadata.status == DataStatus.FORECAST
    assert filtered.metadata.observation_type == ObservationType.FORECAST
    assert filtered.metadata.finality == FinalityStatus.PRELIMINARY

    unfiltered = await service.get_forecast(OperatingReserveForecastRequest())
    assert unfiltered.intervals[0] == forecast
    assert provider.get_operating_reserve_forecast.await_count == 1


@pytest.mark.asyncio
async def test_operating_reserve_activations_filter_and_page() -> None:
    interval_start = datetime(2026, 8, 1, 3, tzinfo=MARKET_TZ)
    activations = [
        OperatingReserveActivationInterval(
            interval_start=interval_start,
            interval_end=interval_start + timedelta(hours=1),
            service_level="STANDBY",
            reserve_type="regulating",
            activated_volume_mw=5,
            weighted_average_activation_price_cad_per_mwh=100,
        ),
        OperatingReserveActivationInterval(
            interval_start=interval_start + timedelta(hours=1),
            interval_end=interval_start + timedelta(hours=2),
            service_level="STANDBY",
            reserve_type="spinning",
            activated_volume_mw=7,
            weighted_average_activation_price_cad_per_mwh=200,
        ),
    ]
    provider = AsyncMock()
    provider.get_operating_reserve_activations.return_value = (
        activations,
        datetime(2026, 8, 2, tzinfo=MARKET_TZ),
        {"source_product": "Standby Activation"},
    )
    service = OperatingReserveService(provider, _settings(), AsyncTTLCache())

    response = await service.get_activations(
        OperatingReserveDateRangeRequest(
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 1),
            reserve_types=["spinning"],
            limit=1,
        )
    )
    assert len(response.intervals) == 1
    assert response.intervals[0].reserve_type == "spinning"
    assert response.page.total == 1
    assert response.metadata.observation_count == 1
    assert response.metadata.source_product == "Standby Activation"


@pytest.mark.asyncio
async def test_operating_reserve_summary_uses_product_specific_and_weighted_values() -> None:
    market_date = date(2026, 8, 1)
    prices = [
        _price(market_date, "active", "regulating", "block_1", active=10, volume=100),
        _price(market_date, "active", "regulating", "block_2", active=20, volume=200),
        _price(market_date, "standby", "regulating", "block_1", blended=30, volume=40),
        _price(market_date, "standby", "regulating", "block_2", blended=50, volume=60),
        _price(market_date, "active", "spinning", "block_1", active=None, volume=None),
        _price(market_date, "standby", "spinning", "block_1", blended=25, volume=10),
    ]
    activation_start = datetime(2026, 8, 1, 2, tzinfo=MARKET_TZ)
    activations = [
        OperatingReserveActivationInterval(
            interval_start=activation_start,
            interval_end=activation_start + timedelta(hours=1),
            service_level="STANDBY",
            reserve_type="regulating",
            activated_volume_mw=5,
            weighted_average_activation_price_cad_per_mwh=100,
        ),
        OperatingReserveActivationInterval(
            interval_start=activation_start + timedelta(hours=1),
            interval_end=activation_start + timedelta(hours=2),
            service_level="STANDBY",
            reserve_type="regulating",
            activated_volume_mw=15,
            weighted_average_activation_price_cad_per_mwh=200,
        ),
        OperatingReserveActivationInterval(
            interval_start=activation_start + timedelta(hours=2),
            interval_end=activation_start + timedelta(hours=3),
            service_level="STANDBY",
            reserve_type="spinning",
            activated_volume_mw=0,
            weighted_average_activation_price_cad_per_mwh=999,
        ),
    ]
    provider = AsyncMock()
    provider.get_operating_reserve_prices.return_value = (
        prices,
        datetime(2026, 8, 2, tzinfo=MARKET_TZ),
        {"source_product": "Operating Reserve Prices"},
    )
    provider.get_operating_reserve_activations.return_value = (
        activations,
        datetime(2026, 8, 2, tzinfo=MARKET_TZ),
        {"source_product": "Operating Reserve Activations"},
    )
    service = OperatingReserveService(provider, _settings(), AsyncTTLCache())

    response = await service.summarize(
        OperatingReserveSummaryRequest(
            start_date=market_date,
            end_date=market_date,
            include_activations=True,
        )
    )
    results = {(item.procurement, item.reserve_type): item for item in response.results}
    active_regulating = results[("active", "regulating")]
    standby_regulating = results[("standby", "regulating")]
    active_spinning = results[("active", "spinning")]
    standby_spinning = results[("standby", "spinning")]

    assert active_regulating.average_price_cad_per_mw == pytest.approx(15)
    assert active_regulating.minimum_price_cad_per_mw == pytest.approx(10)
    assert active_regulating.maximum_price_cad_per_mw == pytest.approx(20)
    assert active_regulating.average_volume_mw == pytest.approx(150)
    assert active_regulating.activated_volume_mw == 0
    assert standby_regulating.average_price_cad_per_mw == pytest.approx(40)
    assert standby_regulating.average_volume_mw == pytest.approx(50)
    assert standby_regulating.activated_volume_mw == pytest.approx(20)
    assert standby_regulating.average_activation_price_cad_per_mwh == pytest.approx(175)
    assert active_spinning.average_price_cad_per_mw is None
    assert active_spinning.average_volume_mw is None
    assert standby_spinning.activated_volume_mw == 0
    assert standby_spinning.average_activation_price_cad_per_mwh is None
    assert response.metadata.provider == ProviderName.DERIVED
    assert response.metadata.observation_type == ObservationType.DERIVED
    assert response.metadata.completeness == DataCompleteness.COMPLETE
    assert response.metadata.observation_count == 4
    assert "volume-weighted" in response.methodology

    without_activations = await service.summarize(
        OperatingReserveSummaryRequest(
            start_date=market_date,
            end_date=market_date,
            include_activations=False,
        )
    )
    assert all(item.activated_volume_mw == 0 for item in without_activations.results)
    assert provider.get_operating_reserve_activations.await_count == 1


@pytest.mark.asyncio
async def test_market_power_mcsinr_selects_latest_populated_and_caches() -> None:
    provider = AsyncMock()
    older = datetime(2026, 8, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 8, 1, 2, tzinfo=UTC)
    provider.get_monthly_cumulative_net_revenue.return_value = (
        [
            McsinrInterval(
                interval_start=older,
                interval_end=older + timedelta(hours=1),
                hour_ending_label="01",
                cumulative_net_revenue_cad=10,
                one_sixth_annualized_unavoidable_costs_cad=100,
                secondary_offer_price_limit_triggered=False,
            ),
            McsinrInterval(
                interval_start=newer,
                interval_end=newer + timedelta(hours=1),
                hour_ending_label="02",
                cumulative_net_revenue_cad=40,
                one_sixth_annualized_unavoidable_costs_cad=100,
                secondary_offer_price_limit_triggered=True,
            ),
            McsinrInterval(
                interval_start=newer + timedelta(hours=1),
                interval_end=newer + timedelta(hours=2),
                hour_ending_label="03",
            ),
        ],
        datetime(2026, 8, 1, 4, tzinfo=UTC),
        {"source_product": "MCSINR"},
    )
    service = MarketPowerService(provider, _settings(), AsyncTTLCache())

    response = await service.get_monthly_cumulative_net_revenue(MarketPowerMitigationRequest())
    assert response.latest_cumulative_net_revenue_cad == 40
    assert response.one_sixth_annualized_unavoidable_costs_cad == 100
    assert response.secondary_offer_price_limit_triggered is True
    assert response.headroom_to_trigger_cad == 60
    assert response.metadata.status == DataStatus.PRELIMINARY
    assert response.metadata.completeness == DataCompleteness.COMPLETE
    assert response.metadata.observation_count == 3
    assert response.metadata.cache_hit is False

    cached = await service.get_monthly_cumulative_net_revenue()
    assert cached.metadata.cache_hit is True
    assert provider.get_monthly_cumulative_net_revenue.await_count == 1


@pytest.mark.asyncio
async def test_market_power_empty_and_missing_threshold_are_explicit() -> None:
    provider = AsyncMock()
    interval_start = datetime(2026, 8, 1, tzinfo=UTC)
    provider.get_monthly_cumulative_net_revenue.return_value = (
        [
            McsinrInterval(
                interval_start=interval_start,
                interval_end=interval_start + timedelta(hours=1),
                hour_ending_label="01",
                cumulative_net_revenue_cad=20,
            )
        ],
        None,
        {},
    )
    provider.get_secondary_offer_price_limit.return_value = ([], None, {})
    service = MarketPowerService(
        provider,
        _settings(cache_ttl_market_power_s=0),
        AsyncTTLCache(),
    )

    no_threshold = await service.get_monthly_cumulative_net_revenue()
    assert no_threshold.latest_cumulative_net_revenue_cad == 20
    assert no_threshold.headroom_to_trigger_cad is None
    assert no_threshold.metadata.available_series == ["mcsinr"]

    empty = await service.get_secondary_offer_price_limit()
    assert empty.limit_in_effect is None
    assert empty.secondary_offer_price_limit_cad_per_mwh is None
    assert empty.metadata.completeness == DataCompleteness.EMPTY
    assert any("No Secondary" in warning for warning in empty.warnings)


@pytest.mark.asyncio
async def test_market_power_secondary_limit_uses_latest_effective_or_notification_time() -> None:
    provider = AsyncMock()
    first = datetime(2026, 8, 1, tzinfo=UTC)
    second = datetime(2026, 8, 2, tzinfo=UTC)
    provider.get_secondary_offer_price_limit.return_value = (
        [
            SecondaryOfferPriceLimitInterval(
                effective_begin=first,
                limit_in_effect=False,
                secondary_offer_price_limit_cad_per_mwh=None,
            ),
            SecondaryOfferPriceLimitInterval(
                public_notification_time=second,
                limit_in_effect=True,
                secondary_offer_price_limit_cad_per_mwh=55,
            ),
            SecondaryOfferPriceLimitInterval(),
        ],
        second,
        {"source_product": "Secondary Offer Price Limit"},
    )
    service = MarketPowerService(provider, _settings(), AsyncTTLCache())

    response = await service.get_secondary_offer_price_limit()
    assert response.limit_in_effect is True
    assert response.secondary_offer_price_limit_cad_per_mwh == 55
    assert response.metadata.observation_count == 3
    assert response.metadata.completeness == DataCompleteness.COMPLETE


@respx.mock
@pytest.mark.asyncio
async def test_archive_client_gets_text_bytes_and_enforces_size(monkeypatch) -> None:
    route = respx.get("https://aeso.box.com/report").mock(
        side_effect=[httpx.Response(200, text="catalog"), httpx.Response(200, content=b"zip")]
    )
    client = AesoArchiveHttpClient(_settings(http_max_retries=0))
    try:
        assert await client.get_text("https://aeso.box.com/report") == "catalog"
        assert await client.get_bytes("https://aeso.box.com/report") == b"zip"
        assert route.call_count == 2

        monkeypatch.setattr("aeso_mcp.providers.archive_http._MAX_ARCHIVE_BYTES", 2)
        respx.get("https://aeso.box.com/large").mock(
            return_value=httpx.Response(200, content=b"too-large")
        )
        with pytest.raises(DataValidationError, match="100 MiB"):
            await client.get_bytes("https://aeso.box.com/large")
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [(400, DataValidationError), (429, RateLimitError), (503, UpstreamUnavailableError)],
)
async def test_archive_client_maps_http_failures(status: int, error_type: type[Exception]) -> None:
    respx.get("https://aeso.box.com/report").mock(return_value=httpx.Response(status))
    client = AesoArchiveHttpClient(_settings(http_max_retries=0))
    try:
        with pytest.raises(error_type):
            await client.get_bytes("https://aeso.box.com/report")
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_archive_client_maps_timeout_and_transport_failure() -> None:
    respx.get("https://aeso.box.com/timeout").mock(side_effect=httpx.ReadTimeout("slow"))
    client = AesoArchiveHttpClient(_settings(http_max_retries=0))
    try:
        with pytest.raises(UpstreamUnavailableError, match="timed out"):
            await client.get_text("https://aeso.box.com/timeout")
    finally:
        await client.aclose()

    respx.get("https://aeso.box.com/connect").mock(side_effect=httpx.ConnectError("offline"))
    client = AesoArchiveHttpClient(_settings(http_max_retries=0))
    try:
        with pytest.raises(UpstreamUnavailableError, match="connect"):
            await client.get_text("https://aeso.box.com/connect")
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_archive_client_follows_relative_redirect() -> None:
    respx.get("https://aeso.box.com/start").mock(
        return_value=httpx.Response(302, headers={"Location": "/final"})
    )
    respx.get("https://aeso.box.com/final").mock(return_value=httpx.Response(200, text="done"))
    client = AesoArchiveHttpClient(_settings(http_max_retries=0))
    try:
        assert await client.get_text("https://aeso.box.com/start") == "done"
    finally:
        await client.aclose()


@respx.mock
@pytest.mark.asyncio
async def test_archive_client_rejects_bad_redirects_and_redirect_loop() -> None:
    respx.get("https://aeso.box.com/missing").mock(return_value=httpx.Response(302))
    client = AesoArchiveHttpClient(_settings(http_max_retries=0))
    try:
        with pytest.raises(DataValidationError, match="omitted Location"):
            await client.get_text("https://aeso.box.com/missing")
    finally:
        await client.aclose()

    respx.get("https://aeso.box.com/foreign").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    client = AesoArchiveHttpClient(_settings(http_max_retries=0))
    try:
        with pytest.raises(DataValidationError, match="allow-list"):
            await client.get_text("https://aeso.box.com/foreign")
    finally:
        await client.aclose()

    respx.get("https://aeso.box.com/loop").mock(
        side_effect=[httpx.Response(302, headers={"Location": "/loop"}) for _ in range(6)]
    )
    client = AesoArchiveHttpClient(_settings(http_max_retries=0))
    try:
        with pytest.raises(DataValidationError, match="redirect safety limit"):
            await client.get_text("https://aeso.box.com/loop")
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://aeso.box.com/report",
        "https://evil.example/report",
        "https://user:pass@aeso.box.com/report",
        "https://aeso.box.com/report?api-key=secret",
        "https://aeso.box.com/report?subscription-key=secret",
        "https://aeso.box.com/report?aeso_api_key=secret",
    ],
)
async def test_archive_client_rejects_urls_outside_public_allowlist(url: str) -> None:
    client = AesoArchiveHttpClient(_settings(http_max_retries=0))
    try:
        with pytest.raises(DataValidationError):
            await client.get_text(url)
    finally:
        await client.aclose()
