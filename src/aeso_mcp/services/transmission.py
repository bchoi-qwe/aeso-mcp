# SPDX-License-Identifier: MIT
"""Transmission outage services (approved + long-range)."""

from __future__ import annotations

from aeso_mcp.config import Settings
from aeso_mcp.errors import InvalidDateRangeError
from aeso_mcp.models.common import DatasetMetadata, DataStatus, ProviderName
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
from aeso_mcp.timeutil import utc_now, validate_range


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

        outages, publication_time, prov = await self._cache.get_or_set(
            cache_key,
            lambda: self._approved.get_approved_transmission_outages(start, end),
            ttl_s=ttl_s,
        )
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
            metadata=DatasetMetadata(
                dataset="Approved Transmission Outages",
                source_product=prov.get("source_product"),
                api_version=prov.get("api_version"),
                retrieved_at=utc_now(),
                status=DataStatus.PRELIMINARY,
                units={},
                observation_granularity="publication",
                request_start=start,
                request_end=end,
                publication_time=publication_time,
                provider=ProviderName(prov.get("provider", ProviderName.AESO_PUBLIC_REPORT.value)),
                observation_count=len(outages),
            ),
            warnings=warnings,
        )

    async def get_long_range_transmission_outages(
        self,
        request: LongRangeTransmissionOutagesRequest | None = None,
    ) -> TransmissionOutagesResponse:
        _ = request or LongRangeTransmissionOutagesRequest()
        outages, publication_time, prov = await self._cache.get_or_set(
            ("long_range_tx_outages", "current"),
            lambda: self._long_range.get_long_range_transmission_outages(),
            ttl_s=self._settings.cache_ttl_long_range_outages_s,
        )
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
            metadata=DatasetMetadata(
                dataset="Long Range Significant Transmission Outages",
                source_product=prov.get("source_product"),
                retrieved_at=utc_now(),
                status=DataStatus.PRELIMINARY,
                units={},
                observation_granularity="publication",
                publication_time=publication_time,
                provider=ProviderName(prov.get("provider", ProviderName.AESO_PUBLIC_REPORT.value)),
                observation_count=len(outages),
            ),
            warnings=warnings,
        )
