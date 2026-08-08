# SPDX-License-Identifier: MIT
"""Direct AESO APIM provider using httpx.

Used for contract tests and as a fallback when a dataset needs behavior
GridStatus does not expose. Prefer GridStatusProvider for production reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from aeso_mcp.errors import DataValidationError, UnsupportedDatasetError
from aeso_mcp.models.assets import AssetRecord
from aeso_mcp.models.common import ProviderName
from aeso_mcp.models.generation import FuelMixComponent, GenerationInterval
from aeso_mcp.models.grid import InterchangePathFlow, OutageRecord
from aeso_mcp.models.prices import PoolPriceInterval, SystemMarginalPriceInterval
from aeso_mcp.providers.csd import parse_csd_payload
from aeso_mcp.providers.http import AesoHttpClient
from aeso_mcp.timeutil import MARKET_TZ, format_aeso_date, in_half_open_range


def _prov(product: str, api_version: str | None = None) -> dict[str, str]:
    meta = {"provider": ProviderName.AESO_APIM.value, "source_product": product}
    if api_version:
        meta["api_version"] = api_version
    return meta


def _parse_utc(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts.astimezone(MARKET_TZ)


class AesoApimProvider:
    """Thin typed wrapper over AESO public APIM endpoints."""

    def __init__(self, http: AesoHttpClient) -> None:
        self._http = http

    async def get_pool_prices(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[PoolPriceInterval], dict[str, str]]:
        start_s = format_aeso_date(start)
        end_s = format_aeso_date(end)
        endpoint = f"poolprice-api/v1.1/price/poolPrice?startDate={start_s}&endDate={end_s}"
        data = await self._http.get_json(endpoint)
        report = _nested(data, "return", "Pool Price Report")
        if report is None:
            report = []
        if not isinstance(report, list):
            raise DataValidationError("Unexpected Pool Price Report shape.")

        intervals: list[PoolPriceInterval] = []
        for item in report:
            if not isinstance(item, dict):
                continue
            begin = item.get("begin_datetime_utc")
            price = item.get("pool_price")
            if begin is None or price is None:
                continue
            start_dt = _parse_utc(str(begin))
            intervals.append(
                PoolPriceInterval(
                    interval_start=start_dt,
                    interval_end=start_dt + timedelta(hours=1),
                    pool_price_cad_per_mwh=float(price),
                    forecast_pool_price_cad_per_mwh=_opt_float(item.get("forecast_pool_price")),
                    rolling_30day_avg_cad_per_mwh=_opt_float(item.get("rolling_30day_avg")),
                )
            )
        intervals = [i for i in intervals if in_half_open_range(i.interval_start, start, end)]
        return intervals, _prov("Pool Price API", "v1.1")

    async def get_system_marginal_prices(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[SystemMarginalPriceInterval], dict[str, str]]:
        start_s = format_aeso_date(start)
        end_s = format_aeso_date(end)
        endpoint = (
            f"systemmarginalprice-api/v1.1/price/systemMarginalPrice"
            f"?startDate={start_s}&endDate={end_s}"
        )
        data = await self._http.get_json(endpoint)
        report = _nested(data, "return", "System Marginal Price Report")
        if report is None:
            report = []
        if not isinstance(report, list):
            raise DataValidationError("Unexpected System Marginal Price Report shape.")

        intervals: list[SystemMarginalPriceInterval] = []
        for item in report:
            if not isinstance(item, dict):
                continue
            begin = item.get("begin_datetime_utc")
            price = item.get("system_marginal_price")
            if begin is None or price is None:
                continue
            start_dt = _parse_utc(str(begin))
            end_raw = item.get("end_datetime_utc")
            end_dt = _parse_utc(str(end_raw)) if end_raw else start_dt + timedelta(minutes=1)
            intervals.append(
                SystemMarginalPriceInterval(
                    interval_start=start_dt,
                    interval_end=end_dt,
                    system_marginal_price_cad_per_mwh=float(price),
                )
            )
        intervals = [i for i in intervals if in_half_open_range(i.interval_start, start, end)]
        return intervals, _prov("System Marginal Price API", "v1.1")

    async def get_load(
        self,
        start: datetime,
        end: datetime,
        *,
        include_forecast: bool = False,
    ) -> tuple[list[dict[str, object]], dict[str, str]]:
        start_s = format_aeso_date(start)
        end_s = format_aeso_date(end)
        endpoint = (
            f"actualforecast-api/v1/load/albertaInternalLoad?startDate={start_s}&endDate={end_s}"
        )
        data = await self._http.get_json(endpoint)
        report = _nested(data, "return", "Actual Forecast Report")
        if report is None:
            report = _nested(data, "return") or []
        if isinstance(report, dict):
            # Some payloads nest further
            for key in ("Actual Forecast Report", "load", "data"):
                if key in report and isinstance(report[key], list):
                    report = report[key]
                    break
        if not isinstance(report, list):
            raise DataValidationError("Unexpected load response shape.")

        rows: list[dict[str, object]] = []
        for item in report:
            if not isinstance(item, dict):
                continue
            begin = item.get("begin_datetime_utc") or item.get("beginDateTimeUTC")
            load = item.get("alberta_internal_load") or item.get("Alberta Internal Load")
            if begin is None or load is None:
                continue
            start_dt = _parse_utc(str(begin))
            forecast = None
            if include_forecast:
                forecast = _opt_float(
                    item.get("forecast_alberta_internal_load")
                    or item.get("alberta_internal_load_forecast")
                )
            rows.append(
                {
                    "interval_start": start_dt,
                    "interval_end": start_dt + timedelta(hours=1),
                    "load_mw": float(load),
                    "load_forecast_mw": forecast,
                }
            )
        rows = [
            r
            for r in rows
            if isinstance(r["interval_start"], datetime)
            and in_half_open_range(r["interval_start"], start, end)
        ]
        return rows, _prov("Alberta Internal Load API", "v1")

    async def get_fuel_mix(self) -> tuple[datetime, list[FuelMixComponent], dict[str, str]]:
        data = await self._http.get_json("currentsupplydemand-api/v2/csd/summary/current")
        observed_at, payload = parse_csd_payload(data)
        components = payload["generation_by_fuel"]
        assert isinstance(components, list)
        return observed_at, components, _prov("Current Supply Demand API", "v2")

    async def get_generation_history(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[GenerationInterval], dict[str, str]]:
        # Direct historical fuel-mix is not generally available on this adapter.
        # Production wiring uses GridStatusProvider for wind/solar history.
        _ = (start, end)
        raise UnsupportedDatasetError(
            "Historical generation is not implemented on the direct APIM provider; "
            "use the GridStatus-backed runtime path (default)."
        )

    async def get_interchange(
        self,
    ) -> tuple[datetime, list[InterchangePathFlow], float, dict[str, str]]:
        data = await self._http.get_json("currentsupplydemand-api/v2/csd/summary/current")
        observed_at, payload = parse_csd_payload(data)
        paths = payload["interchange_paths"]
        net = payload["net_interchange_mw"]
        assert isinstance(paths, list)
        assert isinstance(net, int | float)
        return observed_at, paths, float(net), _prov("Current Supply Demand API", "v2")

    async def get_reserves(self) -> tuple[datetime, dict[str, float | None], dict[str, str]]:
        data = await self._http.get_json("currentsupplydemand-api/v2/csd/summary/current")
        observed_at, payload = parse_csd_payload(data)
        reserves = payload["reserves"]
        assert isinstance(reserves, dict)
        return observed_at, reserves, _prov("Current Supply Demand API", "v2")

    async def get_supply_demand_snapshot(
        self,
    ) -> tuple[datetime, dict[str, object], dict[str, str]]:
        data = await self._http.get_json("currentsupplydemand-api/v2/csd/summary/current")
        observed_at, payload = parse_csd_payload(data)
        return observed_at, payload, _prov("Current Supply Demand API", "v2")

    async def get_assets(
        self,
        *,
        asset_id: str | None = None,
        pool_participant_id: str | None = None,
        operating_status: str | None = None,
        asset_type: str | None = None,
    ) -> tuple[list[AssetRecord], dict[str, str]]:
        params: dict[str, Any] = {}
        if asset_id:
            params["asset_ID"] = asset_id
        if pool_participant_id:
            params["pool_participant_ID"] = pool_participant_id
        if operating_status:
            params["operating_status"] = operating_status
        if asset_type:
            params["asset_type"] = asset_type
        data = await self._http.get_json("assetlist-api/v1/assetlist", params=params or None)
        report = _nested(data, "return", "Asset List") or _nested(data, "return") or []
        if isinstance(report, dict):
            report = report.get("Asset List") or report.get("assets") or []
        if not isinstance(report, list):
            raise DataValidationError("Unexpected asset list shape.")
        assets: list[AssetRecord] = []
        for item in report:
            if not isinstance(item, dict):
                continue
            aid = item.get("asset_ID") or item.get("asset_id")
            if not aid:
                continue
            assets.append(
                AssetRecord(
                    asset_id=str(aid),
                    asset_name=_opt_str(item.get("asset_name")),
                    asset_type=_opt_str(item.get("asset_type")),
                    operating_status=_opt_str(item.get("operating_status")),
                    pool_participant_id=_opt_str(item.get("pool_participant_ID")),
                    pool_participant_name=_opt_str(item.get("pool_participant_name")),
                )
            )
        return assets, _prov("Asset List API", "v1")

    async def get_outages(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[OutageRecord], dict[str, str]]:
        # Outage endpoints vary; prefer GridStatus for reliability in v0.1.
        _ = (start, end)
        raise UnsupportedDatasetError(
            "Generator outages are not implemented on the direct APIM provider; "
            "use the GridStatus-backed runtime path (default)."
        )


def _nested(data: Any, *keys: str) -> Any:
    cur = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
