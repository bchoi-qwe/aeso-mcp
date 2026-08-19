# SPDX-License-Identifier: MIT
"""Transmission outage services (approved + long-range)."""

from __future__ import annotations

from aeso_mcp.config import Settings
from aeso_mcp.errors import InvalidDateRangeError
from aeso_mcp.models.common import DataCompleteness, DataStatus
from aeso_mcp.models.transmission import (
    ApprovedTransmissionOutagesRequest,
    LongRangeTransmissionOutagesRequest,
    TransmissionOutagesResponse,
)
from aeso_mcp.providers.capabilities import (
    ApprovedTransmissionOutageProvider,
    LongRangeTransmissionOutageProvider,
)
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.market import _meta
from aeso_mcp.timeutil import validate_range


class TransmissionService:
    """Transmission planned-outage retrieval with distinct approval semantics."""

    def __init__(
        self,
        *,
        approved_provider: ApprovedTransmissionOutageProvider,
        long_range_provider: LongRangeTransmissionOutageProvider,
        settings: Settings,
        cache: AsyncTTLCache | None = None,
    ) -> None:
        self._approved = approved_provider
        self._long_range = long_range_provider
        self._settings = settings
        self._cache = cache or AsyncTTLCache(max_entries=settings.cache_max_entries)

    async def get_approved_transmission_outages(
        self,
        request: ApprovedTransmissionOutagesRequest,
    ) -> TransmissionOutagesResponse:
        if (request.start is None) ^ (request.end is None):
            raise InvalidDateRangeError(
                "Provide both start and end for historical approved transmission outages, "
                "or omit both for the current publication."
            )
        if request.start is not None and request.end is not None:
            start, end = validate_range(
                request.start,
                request.end,
                max_days=self._settings.max_transmission_outage_history_days,
                label="approved transmission outage history",
            )
            cache_key = ("approved_tx_outages", start.isoformat(), end.isoformat())
            ttl_s = self._settings.cache_ttl_historical_public_report_s
        else:
            start = end = None
            cache_key = ("approved_tx_outages", "latest")
            ttl_s = self._settings.cache_ttl_public_report_s

        cached = await self._cache.get_or_set_with_metadata(
            cache_key,
            lambda: self._approved.get_approved_transmission_outages(start, end),
            ttl_s=ttl_s,
        )
        outages, publication_time, prov = cached.value
        warnings: list[str] = [
            "These are AESO-approved planned transmission outages "
            "(approval_status=approved), not generator outages."
        ]
        if not outages:
            warnings.append("No approved transmission outage records returned.")
        return TransmissionOutagesResponse(
            outages=outages,
            approval_status="approved",
            publication_time=publication_time,
            metadata=_meta(
                dataset="Approved Transmission Outages",
                prov=prov,
                status=DataStatus.PRELIMINARY,
                units={},
                granularity="publication",
                start=start,
                end=end,
                count=len(outages),
                cache_info=cached.info,
                available_series=["approved_transmission_outages"] if outages else [],
                completeness=(DataCompleteness.COMPLETE if outages else DataCompleteness.EMPTY),
            ),
            warnings=warnings,
        )

    async def get_long_range_transmission_outages(
        self,
        request: LongRangeTransmissionOutagesRequest | None = None,
    ) -> TransmissionOutagesResponse:
        _ = request or LongRangeTransmissionOutagesRequest()
        cached = await self._cache.get_or_set_with_metadata(
            ("long_range_tx_outages", "current"),
            lambda: self._long_range.get_long_range_transmission_outages(),
            ttl_s=self._settings.cache_ttl_long_range_outages_s,
        )
        outages, publication_time, prov = cached.value
        warnings = [
            "Long Range Significant Transmission Outages may be tentative and not "
            "AESO-approved (approval_status=tentative). Do not treat as approved outages."
        ]
        if not outages:
            warnings.append("No long-range transmission outage records returned.")
        return TransmissionOutagesResponse(
            outages=outages,
            approval_status="tentative",
            publication_time=publication_time,
            metadata=_meta(
                dataset="Long Range Significant Transmission Outages",
                prov=prov,
                status=DataStatus.PRELIMINARY,
                units={},
                granularity="publication",
                publication_time=publication_time,
                count=len(outages),
                cache_info=cached.info,
                available_series=["long_range_transmission_outages"] if outages else [],
                completeness=(DataCompleteness.COMPLETE if outages else DataCompleteness.EMPTY),
            ),
            warnings=warnings,
        )
