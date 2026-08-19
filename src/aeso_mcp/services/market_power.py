# SPDX-License-Identifier: MIT
"""Market-power mitigation services over AESO public reports."""

from __future__ import annotations

from datetime import datetime

from aeso_mcp.config import Settings
from aeso_mcp.models.common import DataCompleteness, DataStatus
from aeso_mcp.models.market_power import (
    MarketPowerMitigationRequest,
    McsinrResponse,
    SecondaryOfferPriceLimitResponse,
)
from aeso_mcp.providers.public_reports import AesoPublicReportsProvider
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.market import _meta
from aeso_mcp.timeutil import MARKET_TZ, chronological_instant


class MarketPowerService:
    """MCSINR and Secondary Offer Price Limit retrieval."""

    def __init__(
        self,
        provider: AesoPublicReportsProvider,
        settings: Settings,
        cache: AsyncTTLCache | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._cache = cache or AsyncTTLCache(max_entries=settings.cache_max_entries)

    async def get_monthly_cumulative_net_revenue(
        self,
        request: MarketPowerMitigationRequest | None = None,
    ) -> McsinrResponse:
        _ = request or MarketPowerMitigationRequest()
        cached = await self._cache.get_or_set_with_metadata(
            ("mcsinr", "current"),
            lambda: self._provider.get_monthly_cumulative_net_revenue(),
            ttl_s=self._settings.cache_ttl_market_power_s,
        )
        intervals, report_time, prov = cached.value
        # Prefer the chronologically latest populated cumulative value.
        populated = [i for i in intervals if i.cumulative_net_revenue_cad is not None]
        latest = max(populated, key=lambda i: chronological_instant(i.interval_start), default=None)
        threshold = None
        triggered = None
        headroom = None
        if latest is not None:
            threshold = latest.one_sixth_annualized_unavoidable_costs_cad
            triggered = latest.secondary_offer_price_limit_triggered
            if latest.cumulative_net_revenue_cad is not None and threshold is not None:
                headroom = threshold - latest.cumulative_net_revenue_cad
        warnings = [
            "MCSINR is an AESO public ETS report for interim market-power mitigation. "
            "Values may be preliminary within the current month."
        ]
        if not intervals:
            warnings.append("No MCSINR intervals returned.")
        return McsinrResponse(
            report_time=report_time,
            intervals=intervals,
            latest_cumulative_net_revenue_cad=(
                latest.cumulative_net_revenue_cad if latest is not None else None
            ),
            one_sixth_annualized_unavoidable_costs_cad=threshold,
            secondary_offer_price_limit_triggered=triggered,
            headroom_to_trigger_cad=headroom,
            metadata=_meta(
                dataset="Monthly Cumulative Settlement Interval Net Revenue",
                prov=prov,
                status=DataStatus.PRELIMINARY,
                units={
                    "cumulative_net_revenue_cad": "CAD",
                    "one_sixth_annualized_unavoidable_costs_cad": "CAD",
                },
                granularity="1h",
                publication_time=report_time,
                count=len(intervals),
                cache_info=cached.info,
                available_series=["mcsinr"] if intervals else [],
                completeness=(DataCompleteness.COMPLETE if intervals else DataCompleteness.EMPTY),
            ),
            warnings=warnings,
        )

    async def get_secondary_offer_price_limit(
        self,
        request: MarketPowerMitigationRequest | None = None,
    ) -> SecondaryOfferPriceLimitResponse:
        _ = request or MarketPowerMitigationRequest()
        cached = await self._cache.get_or_set_with_metadata(
            ("secondary_offer_price_limit", "current"),
            lambda: self._provider.get_secondary_offer_price_limit(),
            ttl_s=self._settings.cache_ttl_market_power_s,
        )
        intervals, report_time, prov = cached.value
        current = None
        if intervals:
            current = max(
                intervals,
                key=lambda i: chronological_instant(
                    i.effective_begin
                    or i.public_notification_time
                    or datetime.min.replace(tzinfo=MARKET_TZ)
                ),
            )
        warnings = [
            "Secondary Offer Price Limit is an AESO public ETS report. "
            "A null limit means the secondary offer cap is not in effect."
        ]
        if not intervals:
            warnings.append("No Secondary Offer Price Limit rows returned.")
        return SecondaryOfferPriceLimitResponse(
            report_time=report_time,
            intervals=intervals,
            limit_in_effect=current.limit_in_effect if current is not None else None,
            secondary_offer_price_limit_cad_per_mwh=(
                current.secondary_offer_price_limit_cad_per_mwh if current is not None else None
            ),
            metadata=_meta(
                dataset="Secondary Offer Price Limit",
                prov=prov,
                status=DataStatus.PRELIMINARY,
                units={"secondary_offer_price_limit_cad_per_mwh": "CAD/MWh"},
                granularity="publication",
                publication_time=report_time,
                count=len(intervals),
                cache_info=cached.info,
                available_series=["secondary_offer_price_limit"] if intervals else [],
                completeness=(DataCompleteness.COMPLETE if intervals else DataCompleteness.EMPTY),
            ),
            warnings=warnings,
        )
