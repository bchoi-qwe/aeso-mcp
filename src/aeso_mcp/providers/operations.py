# SPDX-License-Identifier: MIT
"""Authenticated AESO APIM provider for operational and market-report datasets."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from aeso_mcp.errors import DataValidationError
from aeso_mcp.models.common import ProviderName
from aeso_mcp.models.operations import (
    EnergyMeritOrderBlock,
    GenerationCapacityInterval,
    IntertieCapabilityInterval,
    IntertieOutageRecord,
    LoadOutageForecastInterval,
    MeteredVolumeInterval,
    OperatingReserveOfferBlock,
    UnitCommitmentDirective,
)
from aeso_mcp.models.research_data import PoolParticipantAgent, PoolParticipantRecord
from aeso_mcp.providers.http import AesoHttpClient
from aeso_mcp.timeutil import MARKET_TZ, parse_aeso_hour_ending, to_market


def _provenance(product: str, api_version: str) -> dict[str, str]:
    return {
        "provider": ProviderName.AESO_APIM.value,
        "source_product": product,
        "api_version": api_version,
    }


class AesoOperationsProvider:
    """Typed adapter for additional products in the official AESO APIM catalog."""

    def __init__(self, http: AesoHttpClient) -> None:
        self._http = http

    async def get_energy_merit_order(
        self, report_date: date
    ) -> tuple[list[EnergyMeritOrderBlock], dict[str, str]]:
        data = await self._http.get_json(
            "energymeritorder-api/v1/meritOrder/energy",
            params={"startDate": report_date.isoformat()},
        )
        snapshots = _find_list(data, "data", "Energy Merit Order Report")
        blocks: list[EnergyMeritOrderBlock] = []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            interval = _first_datetime(
                snapshot, "begin_dateTime_utc", "begin_datetime_utc", "begin_dateTime_mpt"
            )
            if interval is None:
                continue
            for item in snapshot.get("energy_blocks") or []:
                if not isinstance(item, dict):
                    continue
                blocks.append(
                    EnergyMeritOrderBlock(
                        interval_start=interval,
                        import_or_export=_text(item.get("import_or_export")),
                        asset_id=_text(item.get("asset_ID")),
                        block_number=_integer(item.get("block_number")),
                        block_price_cad_per_mwh=_number(item.get("block_price")),
                        from_mw=_number(item.get("from_MW")),
                        to_mw=_number(item.get("to_MW")),
                        block_size_mw=_number(item.get("block_size")),
                        available_mw=_number(item.get("available_MW")),
                        dispatched=_text(item.get("dispatched?")),
                        dispatched_mw=_number(item.get("dispatched_MW")),
                        flexible=_text(item.get("flexible?")),
                        offer_control=_text(item.get("offer_control")),
                    )
                )
        return blocks, _provenance("Energy Merit Order Report", "v1")

    async def get_unit_commitments(
        self, start_date: date, end_date: date
    ) -> tuple[list[UnitCommitmentDirective], dict[str, str]]:
        data = await self._http.get_json(
            "unitcommitmentdata-api/v2/unitCommitment",
            params={"startDate": start_date.isoformat(), "endDate": end_date.isoformat()},
        )
        rows = _find_list(data, "unit_commitment", "Unit Commitment Data")
        directives: list[UnitCommitmentDirective] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            asset_id = _text(item.get("asset_ID"))
            if asset_id is None:
                continue
            directives.append(
                UnitCommitmentDirective(
                    asset_id=asset_id,
                    issued_at=_first_datetime(item, "issued_time_utc", "issued_time_mpt"),
                    begins_at=_first_datetime(item, "begin_time_utc", "begin_time_mpt"),
                    operation_start=_first_datetime(
                        item, "operation_start_time_utc", "operation_start_time_mpt"
                    ),
                    operation_end=_first_datetime(
                        item, "operation_end_time_utc", "operation_end_time_mpt"
                    ),
                )
            )
        return directives, _provenance("Unit Commitment Data API", "v2")

    async def get_generation_capacity(
        self, start_date: date, end_date: date
    ) -> tuple[list[GenerationCapacityInterval], dict[str, str]]:
        data = await self._http.get_json(
            "aiesgencapacity-api/v1/AIESGenCapacity",
            params={"startDate": start_date.isoformat(), "endDate": end_date.isoformat()},
        )
        reports = _capacity_reports(data)
        intervals: list[GenerationCapacityInterval] = []
        for report in reports:
            fuel_type = _text(report.get("fuel_type")) or "Unknown"
            sub_fuel_type = _text(report.get("sub_fuel_type"))
            for item in report.get("Hours") or report.get("hours") or []:
                if not isinstance(item, dict):
                    continue
                interval = _first_datetime(
                    item, "begin_datetime_utc", "begin_dateTime_utc", "begin_datetime_mpt"
                )
                grouping = item.get("outage_grouping")
                if interval is None or not isinstance(grouping, dict):
                    continue
                intervals.append(
                    GenerationCapacityInterval(
                        interval_start=interval,
                        fuel_type=fuel_type,
                        sub_fuel_type=sub_fuel_type,
                        maximum_capability_mw=_number(grouping.get("MC")),
                        mothball_outage_mw=_number(grouping.get("MBO OUT")),
                        operating_outage_mw=_number(grouping.get("OP OUT")),
                        available_capability_mw=_number(grouping.get("AC")),
                    )
                )
        return intervals, _provenance("AIES Gen Capacity API", "v1")

    async def get_load_outage_forecast(
        self, start_date: date, end_date: date
    ) -> tuple[list[LoadOutageForecastInterval], dict[str, str]]:
        data = await self._http.get_json(
            "loadoutageforecast-api/v1/loadOutageReport",
            params={"startDate": start_date.isoformat(), "endDate": end_date.isoformat()},
        )
        rows = _find_list(data, "loadOutagePerHourVO", "Load Outage Forecast Report")
        intervals: list[LoadOutageForecastInterval] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            interval = _first_datetime(item, "begin_datetime_utc", "begin_datetime_mpt")
            value = _number(
                item.get("load_outage_forecast (in MW)") or item.get("load_outage_forecast")
            )
            if interval is not None and value is not None:
                intervals.append(
                    LoadOutageForecastInterval(
                        interval_start=interval,
                        load_outage_forecast_mw=value,
                    )
                )
        return intervals, _provenance("Load Outage Forecast API", "v1")

    async def get_intertie_capability(
        self,
        start_date: date,
        end_date: date,
        *,
        start_hour_ending: int,
        end_hour_ending: int,
        include_versions: bool,
    ) -> tuple[list[IntertieCapabilityInterval], dict[str, str]]:
        data = await self._http.get_json(
            "itc/v1/interchange",
            params={
                "startDate": start_date.strftime("%Y%m%d"),
                "endDate": end_date.strftime("%Y%m%d"),
                "startHE": start_hour_ending,
                "endHE": end_hour_ending,
                "version": str(include_versions).lower(),
            },
        )
        payload = _unwrap_return(data)
        if not isinstance(payload, dict):
            raise DataValidationError("Unexpected Interchange Capability response shape.")
        intervals: list[IntertieCapabilityInterval] = []
        for intertie, report in payload.items():
            if not isinstance(report, dict):
                continue
            allocations = report.get("Allocations") or report.get("allocations") or []
            if isinstance(allocations, dict):
                allocations = [allocations]
            for allocation in allocations:
                if not isinstance(allocation, dict):
                    continue
                hour_ending = _hour_ending(allocation.get("he"))
                if hour_ending is None:
                    continue
                interval = _intertie_interval_start(
                    allocation,
                    hour_ending,
                    raw_hour_ending=_text(allocation.get("he")),
                )
                if interval is None:
                    continue
                for direction in ("import", "export"):
                    values = allocation.get(direction)
                    if not isinstance(values, dict):
                        continue
                    intervals.append(
                        _capability_interval(
                            intertie=str(intertie),
                            interval=interval,
                            hour_ending=hour_ending,
                            direction=direction,
                            flowgate=bool(allocation.get("flowgate", False)),
                            values=values,
                            is_current=True,
                        )
                    )
                    if include_versions:
                        revisions = values.get("version") or []
                        if isinstance(revisions, dict):
                            revisions = [revisions]
                        for revision in revisions:
                            if not isinstance(revision, dict):
                                continue
                            intervals.append(
                                _capability_interval(
                                    intertie=str(intertie),
                                    interval=interval,
                                    hour_ending=hour_ending,
                                    direction=direction,
                                    flowgate=bool(allocation.get("flowgate", False)),
                                    values=revision,
                                    is_current=False,
                                    transfer_type=_text(values.get("transferType")),
                                    effective_at=_parse_datetime(values.get("effectiveLocalTime")),
                                )
                            )
        return intervals, _provenance("Intertie Public Reports", "v1")

    async def get_intertie_outages(
        self, start_date: date, end_date: date
    ) -> tuple[list[IntertieOutageRecord], dict[str, str]]:
        data = await self._http.get_json(
            "itc/v1/outage",
            params={
                "startDate": start_date.strftime("%Y%m%d"),
                "endDate": end_date.strftime("%Y%m%d"),
            },
        )
        payload = _unwrap_return(data)
        if isinstance(payload, dict) and isinstance(payload.get("Outages"), dict):
            payload = payload["Outages"]
        rows: Any = payload.get("Outage", []) if isinstance(payload, dict) else []
        if isinstance(rows, dict):
            rows = [rows]
        outages: list[IntertieOutageRecord] = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            start = _parse_datetime(item.get("fromInLocalTime"))
            end = _parse_datetime(item.get("toInLocalTime"))
            element = _text(item.get("element"))
            affected = item.get("affectedIntertieOrFlowgate") or []
            if isinstance(affected, str):
                affected = [affected]
            if start is None or end is None or element is None:
                continue
            outages.append(
                IntertieOutageRecord(
                    element=element,
                    affected_interties_or_flowgates=[str(value) for value in affected],
                    interval_start=start,
                    interval_end=end,
                )
            )
        return outages, _provenance("Intertie Public Reports", "v1")

    async def get_metered_volumes(
        self,
        start_date: date,
        end_date: date,
        *,
        asset_ids: list[str],
        pool_participant_ids: list[str],
    ) -> tuple[list[MeteredVolumeInterval], dict[str, str]]:
        params: dict[str, Any] = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
        }
        if asset_ids:
            params["asset_ID"] = ",".join(asset_ids)
        if pool_participant_ids:
            params["pool_participant_ID"] = ",".join(pool_participant_ids)
        data = await self._http.get_json(
            "meteredvolume-api/v1/meteredvolume/details", params=params
        )
        reports = _metered_reports(data)
        intervals: list[MeteredVolumeInterval] = []
        for report in reports:
            participant = _text(report.get("pool_participant_ID"))
            assets = report.get("asset_list") or []
            if isinstance(assets, dict):
                assets = [assets]
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                asset_id = _text(asset.get("asset_ID"))
                if asset_id is None:
                    continue
                volumes = asset.get("metered_volume_list") or []
                if isinstance(volumes, dict):
                    volumes = [volumes]
                for item in volumes:
                    if not isinstance(item, dict):
                        continue
                    interval = _first_datetime(item, "begin_date_utc", "begin_date_mpt")
                    volume = _number(item.get("metered_volume"))
                    if interval is None or volume is None:
                        continue
                    intervals.append(
                        MeteredVolumeInterval(
                            pool_participant_id=participant,
                            asset_id=asset_id,
                            asset_class=_text(asset.get("asset_class")),
                            interval_start=interval,
                            metered_volume_mwh=volume,
                        )
                    )
        return intervals, _provenance("Metered Volume Report", "v1")

    async def get_operating_reserve_offer_control(
        self, report_date: date
    ) -> tuple[list[OperatingReserveOfferBlock], dict[str, str]]:
        data = await self._http.get_json(
            "operatingreserveoffercontrol-api/v1/operatingReserveOfferControl",
            params={"startDate": report_date.isoformat()},
        )
        snapshots = _find_list(
            data,
            "Operating Reserve Trade Merit Order",
            "Operating Reserve Offer Control Report",
        )
        blocks: list[OperatingReserveOfferBlock] = []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            interval = _first_datetime(snapshot, "begin_datetime_utc", "begin_datetime_mpt")
            if interval is None:
                continue
            for item in snapshot.get("operating_reserve_blocks") or []:
                if not isinstance(item, dict):
                    continue
                blocks.append(
                    OperatingReserveOfferBlock(
                        interval_start=interval,
                        commodity=_text(item.get("commodity")),
                        product=_text(item.get("product")),
                        asset_id=_text(item.get("asset_ID")),
                        volume_mw=_number(item.get("volume")),
                        active_price_cad_per_mwh=_number(item.get("active_price")),
                        premium_price_cad_per_mwh=_number(item.get("premium_price")),
                        activation_price_cad_per_mwh=_number(item.get("activation_price")),
                        offer_control=_text(item.get("offer_control")),
                    )
                )
        return blocks, _provenance("Operating Reserve Offer Control Report", "v1")

    async def get_pool_participants(
        self,
        *,
        pool_participant_ids: list[str],
        pool_participant_name: str | None,
    ) -> tuple[list[PoolParticipantRecord], dict[str, str]]:
        """Fetch the current Pool Participant API registry.

        The Azure APIM portal documents ``GET /poolparticipantlist`` with
        optional ``pool_participant_ID`` (up to 20 comma-separated IDs) and
        case-sensitive ``pool_participant_name`` filters.  This is a current
        registry; it must not be presented as historical ownership.
        """
        params: dict[str, str] = {}
        if pool_participant_ids:
            params["pool_participant_ID"] = ",".join(pool_participant_ids)
        if pool_participant_name:
            params["pool_participant_name"] = pool_participant_name
        data = await self._http.get_json(
            "poolparticipant-api/v1/poolparticipantlist",
            params=params or None,
        )
        rows = _pool_participant_rows(data)
        participants: list[PoolParticipantRecord] = []
        for item in rows:
            participant_id = _text(item.get("pool_participant_ID"))
            participant_name = _text(item.get("pool_participant_name"))
            if participant_id is None or participant_name is None:
                raise DataValidationError(
                    "Pool Participant API row is missing pool_participant_ID or "
                    "pool_participant_name."
                )
            raw_agents = item.get("agent_list") or []
            if isinstance(raw_agents, dict):
                raw_agents = [raw_agents]
            if not isinstance(raw_agents, list):
                raise DataValidationError("Pool Participant API agent_list has an invalid shape.")
            agents: list[PoolParticipantAgent] = []
            for raw_agent in raw_agents:
                if not isinstance(raw_agent, dict):
                    raise DataValidationError("Pool Participant API agent_list contains a bad row.")
                agent_id = _text(raw_agent.get("agent_ID"))
                if agent_id is None:
                    raise DataValidationError("Pool Participant API agent row is missing agent_ID.")
                agents.append(
                    PoolParticipantAgent(
                        agent_id=agent_id,
                        agent_name=_text(raw_agent.get("agent_name")),
                    )
                )
            participants.append(
                PoolParticipantRecord(
                    pool_participant_id=participant_id,
                    pool_participant_name=participant_name,
                    corporate_contact=_text(item.get("corporate_contact")),
                    agents=agents,
                )
            )
        return participants, _provenance("Pool Participant API", "v1")


def _unwrap_return(data: Any) -> Any:
    if isinstance(data, dict) and "return" in data:
        return data["return"]
    return data


def _capability_interval(
    *,
    intertie: str,
    interval: datetime,
    hour_ending: int,
    direction: str,
    flowgate: bool,
    values: dict[str, Any],
    is_current: bool,
    transfer_type: str | None = None,
    effective_at: datetime | None = None,
) -> IntertieCapabilityInterval:
    return IntertieCapabilityInterval(
        intertie=intertie,
        interval_start=interval,
        hour_ending=hour_ending,
        direction=direction,  # type: ignore[arg-type]
        flowgate=flowgate,
        transfer_type=transfer_type or _text(values.get("transferType")),
        reason=_text(values.get("reason")),
        available_transfer_capability_mw=_number(values.get("atc")),
        total_transfer_capability_mw=_number(values.get("ttc")),
        transmission_reliability_margin_mw=_number(values.get("trmTotal")),
        system_reliability_margin_mw=_number(values.get("trmSystem")),
        allocation_reliability_margin_mw=_number(values.get("trmAllocation")),
        gross_offer_mw=_number(values.get("grossOffer")),
        updated_at=_parse_datetime(values.get("updatedLocalTime")),
        effective_at=effective_at or _parse_datetime(values.get("effectiveLocalTime")),
        is_current=is_current,
        revision_updated_at=_parse_datetime(values.get("versionUpdatedLocaltime")),
    )


def _find_list(data: Any, *keys: str) -> list[Any]:
    payload = _unwrap_return(data)
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise DataValidationError("Unexpected AESO operational report response shape.")
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    for value in payload.values():
        if isinstance(value, list):
            return value
    return []


def _capacity_reports(data: Any) -> list[dict[str, Any]]:
    payload = _unwrap_return(data)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if "fuel_type" in payload:
            return [payload]
        for key in ("data", "AIES Generation Capacity Report", "AIESGenCapacity"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                return [value]
    raise DataValidationError("Unexpected AIES Gen Capacity response shape.")


def _metered_reports(data: Any) -> list[dict[str, Any]]:
    payload = _unwrap_return(data)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if "asset_list" in payload:
            return [payload]
        for key in ("data", "Metered Volume Report"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                return [value]
    raise DataValidationError("Unexpected Metered Volume response shape.")


def _pool_participant_rows(data: Any) -> list[dict[str, Any]]:
    """Normalize the portal's object response and conservative list wrappers."""
    payload = _unwrap_return(data)
    if isinstance(payload, dict):
        if "pool_participant_ID" in payload or "pool_participant_name" in payload:
            return [payload]
        for key in (
            "pool_participant_list",
            "Pool Participant List",
            "poolParticipantList",
            "data",
        ):
            value = payload.get(key)
            if isinstance(value, dict):
                return [value]
            if isinstance(value, list):
                if not all(isinstance(item, dict) for item in value):
                    raise DataValidationError(
                        "Pool Participant API list contains a non-object row."
                    )
                return value
        raise DataValidationError("Unexpected Pool Participant API response shape.")
    if isinstance(payload, list):
        if not all(isinstance(item, dict) for item in payload):
            raise DataValidationError("Pool Participant API list contains a non-object row.")
        return payload
    raise DataValidationError("Unexpected Pool Participant API response shape.")


def _parse_datetime(value: Any, *, assume_utc: bool = False) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%m/%d/%Y %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%m/%d/%Y %H:%M",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC if assume_utc else MARKET_TZ).astimezone(MARKET_TZ)
    return to_market(parsed)


def _first_datetime(item: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        parsed = _parse_datetime(value, assume_utc=key.lower().endswith("_utc"))
        if parsed is not None:
            return parsed
    return None


def _intertie_interval_start(
    item: dict[str, Any],
    hour_ending: int,
    *,
    raw_hour_ending: str | None,
) -> datetime | None:
    value = _text(item.get("date"))
    if value is not None:
        parsed_date: date | None = None
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
            try:
                parsed_date = datetime.strptime(value, fmt).date()
                break
            except ValueError:
                continue
        if parsed_date is not None:
            suffix = "*" if raw_hour_ending and "*" in raw_hour_ending else ""
            label = f"{parsed_date:%m/%d/%Y} {hour_ending}{suffix}"
            try:
                interval_start, _ = parse_aeso_hour_ending(label)
            except ValueError as exc:
                raise DataValidationError(
                    f"Intertie capability row has invalid hour-ending value: {label}."
                ) from exc
            return interval_start
    return _parse_datetime(
        (item.get("import") or {}).get("effectiveLocalTime")
        if isinstance(item.get("import"), dict)
        else None
    )


def _hour_ending(value: Any) -> int | None:
    text = _text(value)
    if text is None:
        return None
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        return None
    hour = int(digits)
    return hour if 1 <= hour <= 24 else None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
