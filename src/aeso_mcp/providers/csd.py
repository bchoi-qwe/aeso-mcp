# SPDX-License-Identifier: MIT
"""Shared Current Supply Demand payload parsing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aeso_mcp.errors import DataValidationError
from aeso_mcp.models.generation import FuelMixComponent
from aeso_mcp.models.grid import InterchangePathFlow
from aeso_mcp.timeutil import MARKET_TZ, to_market


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_csd_payload(data: Any) -> tuple[datetime, dict[str, object]]:
    """Normalize a raw Current Supply Demand JSON payload into snapshot fields."""
    if not isinstance(data, dict) or not isinstance(data.get("return"), dict):
        raise DataValidationError("Unexpected Current Supply Demand response shape.")
    payload = data["return"]
    observed_raw = payload.get("effective_datetime_utc")
    if observed_raw is None:
        raise DataValidationError("CSD response missing effective_datetime_utc.")
    observed_at = to_market(datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00")))
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=MARKET_TZ)

    components: list[FuelMixComponent] = []
    for item in payload.get("generation_data_list") or []:
        if not isinstance(item, dict):
            continue
        fuel = str(item.get("fuel_type", "Unknown")).replace("_", " ").title()
        net = _safe_float(item.get("aggregated_net_generation"))
        if net is None:
            continue
        components.append(
            FuelMixComponent(
                fuel_type=fuel,
                generation_mw=net,
                maximum_capability_mw=_safe_float(item.get("aggregated_maximum_capability")),
            )
        )

    paths: list[InterchangePathFlow] = []
    for item in payload.get("interchange_list") or []:
        if not isinstance(item, dict):
            continue
        flow = _safe_float(item.get("actual_flow"))
        if flow is None:
            continue
        path = str(item.get("path", "Unknown")).strip()
        if path.endswith(" Flow"):
            path = path[: -len(" Flow")].strip()
        paths.append(InterchangePathFlow(path=path, flow_mw=flow))
    net = sum(p.flow_mw for p in paths)

    reserves = {
        "contingency_reserve_required_mw": _safe_float(payload.get("contingency_reserve_required")),
        "dispatched_contingency_reserve_total_mw": _safe_float(
            payload.get("dispatched_contigency_reserve_total")
        ),
        "dispatched_contingency_reserve_gen_mw": _safe_float(
            payload.get("dispatched_contingency_reserve_gen")
        ),
        "dispatched_contingency_reserve_other_mw": _safe_float(
            payload.get("dispatched_contingency_reserve_other")
        ),
        "fast_frequency_response_dispatched_mw": _safe_float(payload.get("ffr_armed_dispatch")),
        "fast_frequency_response_offered_mw": _safe_float(payload.get("ffr_offered_volume")),
        "long_lead_time_volume_mw": _safe_float(payload.get("long_lead_time_volume")),
    }

    load_mw = _safe_float(payload.get("alberta_internal_load"))
    if load_mw is None:
        load_mw = _safe_float(payload.get("total_ail"))

    return observed_at, {
        "generation_by_fuel": components,
        "total_generation_mw": sum(c.generation_mw for c in components),
        "interchange_paths": paths,
        "net_interchange_mw": net,
        "reserves": reserves,
        "alberta_internal_load_mw": load_mw,
    }
