# SPDX-License-Identifier: MIT
"""GridStatus-backed AESO data provider.

GridStatus already implements well-tested AESO APIM adapters. We wrap its
synchronous client with ``asyncio.to_thread`` so MCP handlers stay non-blocking,
and normalize DataFrames into domain models at this boundary.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from aeso_mcp.config import Settings
from aeso_mcp.errors import (
    AesoMcpError,
    AuthenticationError,
    DataValidationError,
    UpstreamUnavailableError,
)
from aeso_mcp.models.assets import AssetRecord
from aeso_mcp.models.common import ProviderName
from aeso_mcp.models.generation import FuelMixComponent, GenerationInterval
from aeso_mcp.models.grid import GeneratorOutageInterval, InterchangePathFlow
from aeso_mcp.models.prices import PoolPriceInterval, SystemMarginalPriceInterval
from aeso_mcp.providers.csd import parse_csd_payload
from aeso_mcp.providers.http import AesoHttpClient
from aeso_mcp.timeutil import MARKET_TZ, in_half_open_range

logger = logging.getLogger(__name__)


def _provenance(product: str, api_version: str | None = None) -> dict[str, str]:
    meta = {
        "provider": ProviderName.GRIDSTATUS.value,
        "source_product": product,
    }
    if api_version:
        meta["api_version"] = api_version
    return meta


def _translate_gridstatus_error(exc: Exception) -> Exception:
    message = str(exc)
    lowered = message.lower()
    # Never include secrets; GridStatus shouldn't embed the key, but be defensive.
    if "api key" in lowered and "required" in lowered:
        return AuthenticationError(
            "AESO API key is missing. Set AESO_API_KEY before starting the server."
        )
    if "401" in message or "403" in message or "access denied" in lowered:
        return AuthenticationError(
            "AESO API authentication failed. Verify AESO_API_KEY and product subscription."
        )
    if "timeout" in lowered or "timed out" in lowered:
        return UpstreamUnavailableError("AESO API request timed out.")
    if "connection" in lowered or "failed to connect" in lowered:
        return UpstreamUnavailableError("Failed to connect to AESO API.")
    if "status 5" in lowered or "500" in message or "502" in message or "503" in message:
        return UpstreamUnavailableError(f"AESO API unavailable: {type(exc).__name__}")
    return UpstreamUnavailableError(f"AESO data retrieval failed: {type(exc).__name__}")


class GridStatusProvider:
    """Adapter around ``gridstatus.AESO``."""

    def __init__(
        self,
        settings: Settings,
        *,
        apim_http: AesoHttpClient | None = None,
    ) -> None:
        self._settings = settings
        self._client: Any | None = None
        self._apim_http = apim_http

    def _get_client(self) -> Any:
        if self._client is None:
            from gridstatus import AESO

            self._client = AESO(api_key=self._settings.api_key_value)
        return self._client

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        def call() -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                raise _translate_gridstatus_error(exc) from exc

        return await asyncio.to_thread(call)

    async def get_pool_prices(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[PoolPriceInterval], dict[str, str]]:
        client = self._get_client()
        df = await self._run(client.get_pool_price, date=start, end=end)
        intervals = _parse_pool_prices(df)
        # Filter to requested half-open range in true elapsed order (DST-safe).
        intervals = [i for i in intervals if in_half_open_range(i.interval_start, start, end)]
        return intervals, _provenance("Pool Price API", "v1.1")

    async def get_system_marginal_prices(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[SystemMarginalPriceInterval], dict[str, str]]:
        client = self._get_client()
        df = await self._run(client.get_system_marginal_price, date=start, end=end)
        intervals = _parse_smp(df)
        intervals = [i for i in intervals if in_half_open_range(i.interval_start, start, end)]
        return intervals, _provenance("System Marginal Price API", "v1.1")

    async def get_load(
        self,
        start: datetime,
        end: datetime,
        *,
        include_forecast: bool = False,
    ) -> tuple[list[dict[str, object]], dict[str, str]]:
        client = self._get_client()
        df = await self._run(client.get_load, date=start, end=end)
        rows = _parse_load(df)
        if include_forecast:
            try:
                forecast_df = await self._run(client.get_load_forecast, date=start, end=end)
                forecast_rows = {
                    r["interval_start"]: r.get("load_forecast_mw")
                    for r in _parse_load_forecast(forecast_df)
                }
                for row in rows:
                    key = row["interval_start"]
                    if key in forecast_rows:
                        row["load_forecast_mw"] = forecast_rows[key]
            except AuthenticationError:
                raise
            except AesoMcpError:
                # Forecast is optional enrichment; keep actual load if forecast fails.
                logger.warning("load_forecast_unavailable")
        rows = [
            r
            for r in rows
            if isinstance(r["interval_start"], datetime)
            and in_half_open_range(r["interval_start"], start, end)
        ]
        return rows, _provenance("Alberta Internal Load API", "v1")

    async def get_fuel_mix(self) -> tuple[datetime, list[FuelMixComponent], dict[str, str]]:
        client = self._get_client()
        df = await self._run(client.get_fuel_mix)
        observed_at, components = _parse_fuel_mix(df)
        return observed_at, components, _provenance("Current Supply Demand API", "v2")

    async def get_generation_history(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[GenerationInterval], dict[str, str]]:
        client = self._get_client()
        intervals: list[GenerationInterval] = []
        failures: list[AesoMcpError] = []
        for fuel, method in (
            ("Wind", client.get_wind_hourly),
            ("Solar", client.get_solar_hourly),
        ):
            try:
                df = await self._run(method, date=start, end=end)
                intervals.extend(_parse_renewable_hourly(df, fuel_type=fuel))
            except AuthenticationError:
                raise
            except AesoMcpError as exc:
                # One renewable series may be temporarily unavailable; keep the other.
                logger.warning("generation_history_unavailable fuel=%s error=%s", fuel, exc.code)
                failures.append(exc)
        if not intervals and failures:
            raise failures[-1]
        intervals = [i for i in intervals if in_half_open_range(i.interval_start, start, end)]
        return intervals, _provenance("Wind/Solar Generation API")

    async def get_interchange(
        self,
    ) -> tuple[datetime, list[InterchangePathFlow], float, dict[str, str]]:
        client = self._get_client()
        df = await self._run(client.get_interchange)
        return (*_parse_interchange(df), _provenance("Current Supply Demand API", "v2"))

    async def get_reserves(self) -> tuple[datetime, dict[str, float | None], dict[str, str]]:
        client = self._get_client()
        df = await self._run(client.get_reserves)
        return (*_parse_reserves(df), _provenance("Current Supply Demand API", "v2"))

    async def get_supply_demand_snapshot(
        self,
    ) -> tuple[datetime, dict[str, object], dict[str, str]]:
        """Fetch Current Supply Demand once via authenticated APIM (not GridStatus private APIs)."""
        if self._apim_http is None:
            raise UpstreamUnavailableError(
                "APIM HTTP client is required for market snapshot CSD retrieval."
            )
        data = await self._apim_http.get_json("currentsupplydemand-api/v2/csd/summary/current")
        observed_at, payload = parse_csd_payload(data)
        return observed_at, payload, _provenance("Current Supply Demand API", "v2")

    async def get_assets(
        self,
        *,
        asset_id: str | None = None,
        pool_participant_id: str | None = None,
        operating_status: str | None = None,
        asset_type: str | None = None,
    ) -> tuple[list[AssetRecord], dict[str, str]]:
        client = self._get_client()
        df = await self._run(
            client.get_asset_list,
            asset_id=asset_id,
            pool_participant_id=pool_participant_id,
            operating_status=operating_status,
            asset_type=asset_type,
        )
        return _parse_assets(df), _provenance("Asset List API", "v1")

    async def get_outages(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[GeneratorOutageInterval], dict[str, str]]:
        client = self._get_client()
        df = await self._run(client.get_generator_outages_hourly, date=start, end=end)
        outages = _parse_generator_outage_intervals(df)
        outages = [o for o in outages if in_half_open_range(o.interval_start, start, end)]
        return outages, _provenance("Generator Outages API")


def _series_to_market_dt(value: Any) -> datetime:
    ts = pd.Timestamp(value)
    ts = ts.tz_localize(MARKET_TZ) if ts.tzinfo is None else ts.tz_convert(MARKET_TZ)
    result = ts.to_pydatetime()
    if not isinstance(result, datetime):
        raise DataValidationError("Invalid timestamp value in AESO response.")
    return result


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def _row_has_interval_end(df: pd.DataFrame, row: Any) -> bool:
    return "Interval End" in df.columns and _is_present(row.get("Interval End"))


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _require_float(value: Any, field: str) -> float:
    parsed = _safe_float(value)
    if parsed is None:
        raise DataValidationError(f"Missing or invalid numeric field: {field}")
    return parsed


def _parse_pool_prices(df: pd.DataFrame) -> list[PoolPriceInterval]:
    if df is None or df.empty:
        return []
    intervals: list[PoolPriceInterval] = []
    for _, row in df.iterrows():
        start = _series_to_market_dt(row["Interval Start"])
        end = (
            _series_to_market_dt(row["Interval End"])
            if _row_has_interval_end(df, row)
            else start + timedelta(hours=1)
        )
        intervals.append(
            PoolPriceInterval(
                interval_start=start,
                interval_end=end,
                pool_price_cad_per_mwh=_require_float(row.get("Pool Price"), "Pool Price"),
                forecast_pool_price_cad_per_mwh=_safe_float(row.get("Forecast Pool Price")),
                rolling_30day_avg_cad_per_mwh=_safe_float(
                    row.get("Rolling 30 Day Average Pool Price")
                ),
            )
        )
    return intervals


def _parse_smp(df: pd.DataFrame) -> list[SystemMarginalPriceInterval]:
    if df is None or df.empty:
        return []
    price_col = "System Marginal Price" if "System Marginal Price" in df.columns else None
    if price_col is None:
        for candidate in df.columns:
            if "marginal" in str(candidate).lower() or "smp" in str(candidate).lower():
                price_col = candidate
                break
    if price_col is None:
        raise DataValidationError("SMP response missing price column.")

    intervals: list[SystemMarginalPriceInterval] = []
    for _, row in df.iterrows():
        start = _series_to_market_dt(row["Interval Start"])
        if _row_has_interval_end(df, row):
            end = _series_to_market_dt(row["Interval End"])
        else:
            end = start + timedelta(minutes=1)
        intervals.append(
            SystemMarginalPriceInterval(
                interval_start=start,
                interval_end=end,
                system_marginal_price_cad_per_mwh=_require_float(row.get(price_col), price_col),
            )
        )
    return intervals


def _parse_load(df: pd.DataFrame) -> list[dict[str, object]]:
    if df is None or df.empty:
        return []
    time_col = "Interval Start" if "Interval Start" in df.columns else "Time"
    load_col = None
    for candidate in ("Alberta Internal Load", "Load", "AIL"):
        if candidate in df.columns:
            load_col = candidate
            break
    if load_col is None:
        # Fall back to first numeric column after time
        numeric = [c for c in df.columns if c != time_col]
        load_col = numeric[0] if numeric else None
    if load_col is None:
        raise DataValidationError("Load response missing load column.")

    rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        start = _series_to_market_dt(row[time_col])
        end = (
            _series_to_market_dt(row["Interval End"])
            if _row_has_interval_end(df, row)
            else start + timedelta(hours=1)
        )
        rows.append(
            {
                "interval_start": start,
                "interval_end": end,
                "load_mw": _require_float(row.get(load_col), load_col),
                "load_forecast_mw": None,
            }
        )
    return rows


def _parse_load_forecast(df: pd.DataFrame) -> list[dict[str, object]]:
    if df is None or df.empty:
        return []
    time_col = "Interval Start" if "Interval Start" in df.columns else "Time"
    forecast_col = None
    for candidate in ("Load Forecast", "Alberta Internal Load Forecast", "Forecast"):
        if candidate in df.columns:
            forecast_col = candidate
            break
    if forecast_col is None:
        return []
    rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "interval_start": _series_to_market_dt(row[time_col]),
                "load_forecast_mw": _safe_float(row.get(forecast_col)),
            }
        )
    return rows


def _parse_fuel_mix(df: pd.DataFrame) -> tuple[datetime, list[FuelMixComponent]]:
    if df is None or df.empty:
        raise DataValidationError("Fuel mix response was empty.")
    row = df.iloc[0]
    observed_at = _series_to_market_dt(row["Time"] if "Time" in df.columns else df.index[0])
    skip = {"Time", "Interval Start", "Interval End"}
    components: list[FuelMixComponent] = []
    for col in df.columns:
        if col in skip:
            continue
        value = _safe_float(row[col])
        if value is None:
            continue
        components.append(FuelMixComponent(fuel_type=str(col), generation_mw=value))
    return observed_at, components


def _parse_renewable_hourly(df: pd.DataFrame, *, fuel_type: str) -> list[GenerationInterval]:
    if df is None or df.empty:
        return []
    time_col = "Interval Start" if "Interval Start" in df.columns else "Time"
    gen_col = None
    for candidate in df.columns:
        name = str(candidate).lower()
        if "generation" in name or "mw" in name or candidate in {"Wind", "Solar"}:
            gen_col = candidate
            break
    if gen_col is None:
        numeric = [c for c in df.columns if c != time_col]
        gen_col = numeric[0] if numeric else None
    if gen_col is None:
        return []

    intervals: list[GenerationInterval] = []
    for _, row in df.iterrows():
        value = _safe_float(row.get(gen_col))
        if value is None:
            continue
        start = _series_to_market_dt(row[time_col])
        end = (
            _series_to_market_dt(row["Interval End"])
            if _row_has_interval_end(df, row)
            else start + timedelta(hours=1)
        )
        intervals.append(
            GenerationInterval(
                interval_start=start,
                interval_end=end,
                fuel_type=fuel_type,
                generation_mw=value,
            )
        )
    return intervals


def _parse_interchange(
    df: pd.DataFrame,
) -> tuple[datetime, list[InterchangePathFlow], float]:
    if df is None or df.empty:
        raise DataValidationError("Interchange response was empty.")
    row = df.iloc[0]
    observed_at = _series_to_market_dt(row["Time"] if "Time" in df.columns else df.index[0])
    skip = {"Time", "Net Interchange", "Net Interchange Flow"}
    paths: list[InterchangePathFlow] = []
    for col in df.columns:
        if col in skip:
            continue
        value = _safe_float(row[col])
        if value is None:
            continue
        paths.append(InterchangePathFlow(path=_normalize_interchange_path(str(col)), flow_mw=value))
    net = _safe_float(row.get("Net Interchange"))
    if net is None:
        net = _safe_float(row.get("Net Interchange Flow"))
    if net is None:
        net = sum(p.flow_mw for p in paths)
    return observed_at, paths, float(net)


def _parse_reserves(df: pd.DataFrame) -> tuple[datetime, dict[str, float | None]]:
    if df is None or df.empty:
        raise DataValidationError("Reserves response was empty.")
    row = df.iloc[0]
    observed_at = _series_to_market_dt(row["Time"] if "Time" in df.columns else df.index[0])
    mapping = {
        "contingency_reserve_required_mw": "Contingency Reserve Required",
        "dispatched_contingency_reserve_total_mw": "Dispatched Contingency Reserve Total",
        "dispatched_contingency_reserve_gen_mw": "Dispatched Contingency Reserve Generation",
        "dispatched_contingency_reserve_other_mw": "Dispatched Contingency Reserve Other",
        "fast_frequency_response_dispatched_mw": "Fast Frequency Response Dispatched",
        "fast_frequency_response_offered_mw": "Fast Frequency Response Offered",
        "long_lead_time_volume_mw": "Long Lead Time Volume",
    }
    values: dict[str, float | None] = {
        key: _safe_float(row.get(col)) for key, col in mapping.items()
    }
    return observed_at, values


def _extract_ail_from_supply(df: pd.DataFrame) -> float | None:
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    for col in ("Alberta Internal Load", "AIL", "Load"):
        if col in df.columns:
            return _safe_float(row[col])
    # Search case-insensitive
    for col in df.columns:
        if "internal load" in str(col).lower() or str(col).lower() == "ail":
            return _safe_float(row[col])
    return None


def _parse_assets(df: pd.DataFrame) -> list[AssetRecord]:
    if df is None or df.empty:
        return []
    records: list[AssetRecord] = []
    for _, row in df.iterrows():
        asset_id = row.get("Asset ID")
        if asset_id is None or (isinstance(asset_id, float) and pd.isna(asset_id)):
            continue
        records.append(
            AssetRecord(
                asset_id=str(asset_id),
                asset_name=_optional_str(row.get("Asset Name")),
                asset_type=_optional_str(row.get("Asset Type")),
                operating_status=_optional_str(row.get("Operating Status")),
                pool_participant_id=_optional_str(row.get("Pool Participant ID")),
                pool_participant_name=_optional_str(row.get("Pool Participant Name")),
                net_to_grid=_optional_bool(row.get("Net To Grid Asset Flag")),
                includes_storage=_optional_bool(row.get("Asset Include Storage Flag")),
            )
        )
    return records


def _parse_generator_outage_intervals(df: pd.DataFrame) -> list[GeneratorOutageInterval]:
    """Parse GridStatus aggregated hourly generator outage capacity by technology."""
    if df is None or df.empty:
        return []
    if "Interval Start" not in df.columns:
        raise DataValidationError(
            "Generator outages response missing Interval Start; upstream schema may have changed."
        )
    if "Total Outage" not in df.columns:
        raise DataValidationError(
            "Generator outages response missing Total Outage; expected aggregated "
            "hourly capacity by fuel/technology (not per-asset rows)."
        )

    records: list[GeneratorOutageInterval] = []
    for _, row in df.iterrows():
        if not _is_present(row.get("Interval Start")):
            continue
        start = _series_to_market_dt(row["Interval Start"])
        end = (
            _series_to_market_dt(row["Interval End"])
            if _row_has_interval_end(df, row)
            else start + timedelta(hours=1)
        )
        pub = (
            _series_to_market_dt(row["Publish Time"])
            if "Publish Time" in df.columns and _is_present(row.get("Publish Time"))
            else None
        )
        total = _safe_float(row.get("Total Outage"))
        if total is None:
            continue
        records.append(
            GeneratorOutageInterval(
                interval_start=start,
                interval_end=end,
                publication_time=pub,
                total_outage_mw=total,
                mothball_outage_mw=_safe_float(row.get("Mothball Outage")) or 0.0,
                simple_cycle_mw=_safe_float(row.get("Simple Cycle")) or 0.0,
                combined_cycle_mw=_safe_float(row.get("Combined Cycle")) or 0.0,
                cogeneration_mw=_safe_float(row.get("Cogeneration")) or 0.0,
                gas_fired_steam_mw=_safe_float(row.get("Gas Fired Steam")) or 0.0,
                coal_mw=_safe_float(row.get("Coal")) or 0.0,
                hydro_mw=_safe_float(row.get("Hydro")) or 0.0,
                wind_mw=_safe_float(row.get("Wind")) or 0.0,
                solar_mw=_safe_float(row.get("Solar")) or 0.0,
                energy_storage_mw=_safe_float(row.get("Energy Storage")) or 0.0,
                biomass_and_other_mw=_safe_float(row.get("Biomass and Other")) or 0.0,
            )
        )
    return records


def _normalize_interchange_path(name: str) -> str:
    text = name.strip()
    if text.endswith(" Flow"):
        return text[: -len(" Flow")].strip()
    return text


def _optional_str(value: Any) -> str | None:
    if value is None or not _is_present(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: Any) -> bool | None:
    if value is None or not _is_present(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"y", "yes", "true", "1"}:
        return True
    if text in {"n", "no", "false", "0"}:
        return False
    return None
