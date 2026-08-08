# SPDX-License-Identifier: MIT
"""Market-power mitigation public-report models (MCSINR / secondary offer cap)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from aeso_mcp.models.common import DatasetMetadata, WarningMixin


class McsinrInterval(BaseModel):
    """One settlement-interval row from the MCSINR public report."""

    model_config = ConfigDict(extra="forbid")

    interval_start: datetime
    interval_end: datetime
    hour_ending_label: str
    cumulative_net_revenue_cad: float | None = None
    one_sixth_annualized_unavoidable_costs_cad: float | None = None
    secondary_offer_price_limit_triggered: bool | None = None


class SecondaryOfferPriceLimitInterval(BaseModel):
    """One effective window from the Secondary Offer Price Limit report."""

    model_config = ConfigDict(extra="forbid")

    effective_begin: datetime | None = None
    effective_end: datetime | None = None
    begin_label: str | None = None
    end_label: str | None = None
    limit_in_effect: bool | None = None
    secondary_offer_price_limit_cad_per_mwh: float | None = None
    public_notification_time: datetime | None = None


class MarketPowerMitigationRequest(BaseModel):
    """Request the current market-power mitigation public reports."""

    model_config = ConfigDict(extra="forbid")

    # Reserved for historical date windows once HistoricalMCSINR parameters are stable.
    current_only: bool = Field(
        default=True,
        description="v0.2 fetches the current ETS publication only.",
    )


class McsinrResponse(WarningMixin):
    """Monthly Cumulative Settlement Interval Net Revenue publication."""

    report_time: datetime | None = None
    intervals: list[McsinrInterval]
    latest_cumulative_net_revenue_cad: float | None = None
    one_sixth_annualized_unavoidable_costs_cad: float | None = None
    secondary_offer_price_limit_triggered: bool | None = None
    headroom_to_trigger_cad: float | None = None
    metadata: DatasetMetadata


class SecondaryOfferPriceLimitResponse(WarningMixin):
    """Secondary Offer Price Limit publication."""

    report_time: datetime | None = None
    intervals: list[SecondaryOfferPriceLimitInterval]
    limit_in_effect: bool | None = None
    secondary_offer_price_limit_cad_per_mwh: float | None = None
    metadata: DatasetMetadata
