# SPDX-License-Identifier: MIT
"""Contract-shape tests for authenticated operational AESO APIM products."""

from __future__ import annotations

from datetime import date
from typing import cast
from unittest.mock import AsyncMock

import pytest

from aeso_mcp.providers.http import AesoHttpClient
from aeso_mcp.providers.operations import AesoOperationsProvider


def _provider(payload: object) -> tuple[AesoOperationsProvider, AsyncMock]:
    http = AsyncMock()
    http.get_json.return_value = payload
    return AesoOperationsProvider(cast(AesoHttpClient, http)), http


@pytest.mark.asyncio
async def test_energy_merit_order_normalizes_blocks_and_uses_catalog_path() -> None:
    provider, http = _provider(
        {
            "return": {
                "data": [
                    {
                        "begin_dateTime_utc": "2024-01-15T07:00:00Z",
                        "energy_blocks": [
                            {
                                "asset_ID": "ASSET1",
                                "block_number": 2,
                                "block_price": "125.50",
                                "from_MW": 10,
                                "to_MW": 20,
                                "block_size": 10,
                                "available_MW": 8,
                                "dispatched?": "Y",
                                "dispatched_MW": 5,
                                "flexible?": "N",
                                "offer_control": "OC",
                            }
                        ],
                    }
                ]
            }
        }
    )

    blocks, provenance = await provider.get_energy_merit_order(date(2024, 1, 15))

    assert blocks[0].asset_id == "ASSET1"
    assert blocks[0].block_price_cad_per_mwh == 125.5
    assert blocks[0].interval_start.hour == 0
    assert provenance["api_version"] == "v1"
    http.get_json.assert_awaited_once_with(
        "energymeritorder-api/v1/meritOrder/energy",
        params={"startDate": "2024-01-15"},
    )


@pytest.mark.asyncio
async def test_unit_commitment_normalizes_directive_times() -> None:
    provider, http = _provider(
        {
            "unit_commitment": [
                {
                    "asset_ID": "GEN1",
                    "issued_time_utc": "2024-07-02T01:00:00Z",
                    "begin_time_utc": "2024-07-02T02:00:00Z",
                    "operation_start_time_utc": "2024-07-02T03:00:00Z",
                    "operation_end_time_utc": "2024-07-02T05:00:00Z",
                }
            ]
        }
    )

    rows, _ = await provider.get_unit_commitments(date(2024, 7, 1), date(2024, 7, 2))

    assert rows[0].asset_id == "GEN1"
    assert rows[0].operation_start is not None
    http.get_json.assert_awaited_once_with(
        "unitcommitmentdata-api/v2/unitCommitment",
        params={"startDate": "2024-07-01", "endDate": "2024-07-02"},
    )


@pytest.mark.asyncio
async def test_generation_capacity_normalizes_fuel_hour_grouping() -> None:
    provider, _ = _provider(
        {
            "return": [
                {
                    "fuel_type": "GAS",
                    "sub_fuel_type": "COMBINED CYCLE",
                    "Hours": [
                        {
                            "begin_datetime_utc": "2024-01-15T07:00:00Z",
                            "outage_grouping": {
                                "MC": 1000,
                                "MBO OUT": 20,
                                "OP OUT": 100,
                                "AC": 880,
                            },
                        }
                    ],
                }
            ]
        }
    )

    rows, _ = await provider.get_generation_capacity(date(2024, 1, 15), date(2024, 1, 15))

    assert rows[0].fuel_type == "GAS"
    assert rows[0].available_capability_mw == 880
    assert rows[0].operating_outage_mw == 100


@pytest.mark.asyncio
async def test_load_outage_forecast_normalizes_documented_field_name() -> None:
    provider, _ = _provider(
        {
            "return": {
                "loadOutagePerHourVO": [
                    {
                        "begin_datetime_utc": "2024-01-15T07:00:00Z",
                        "load_outage_forecast (in MW)": 125,
                    }
                ]
            }
        }
    )

    rows, _ = await provider.get_load_outage_forecast(date(2024, 1, 15), date(2024, 1, 15))

    assert rows[0].load_outage_forecast_mw == 125


@pytest.mark.asyncio
async def test_intertie_capability_flattens_paths_and_directions() -> None:
    provider, http = _provider(
        {
            "BcIntertie": {
                "Allocations": [
                    {
                        "flowgate": False,
                        "date": "2024-01-15",
                        "he": "1",
                        "import": {
                            "atc": 500,
                            "ttc": 600,
                            "trmTotal": 100,
                            "version": [
                                {
                                    "atc": 450,
                                    "ttc": 550,
                                    "trmTotal": 100,
                                    "versionUpdatedLocaltime": "2024-01-14T23:30:00",
                                }
                            ],
                        },
                        "export": {"atc": 400, "ttc": 450, "trmTotal": 50},
                    }
                ]
            }
        }
    )

    rows, _ = await provider.get_intertie_capability(
        date(2024, 1, 15),
        date(2024, 1, 15),
        start_hour_ending=1,
        end_hour_ending=24,
        include_versions=True,
    )

    assert [
        (row.direction, row.available_transfer_capability_mw, row.is_current) for row in rows
    ] == [
        ("import", 500.0, True),
        ("import", 450.0, False),
        ("export", 400.0, True),
    ]
    http.get_json.assert_awaited_once_with(
        "itc/v1/interchange",
        params={
            "startDate": "20240115",
            "endDate": "20240115",
            "startHE": 1,
            "endHE": 24,
            "version": "true",
        },
    )


@pytest.mark.asyncio
async def test_intertie_capability_preserves_fall_back_hour_fold() -> None:
    provider, _ = _provider(
        {
            "BcIntertie": {
                "Allocations": [
                    {
                        "date": "2024-11-03",
                        "he": "2",
                        "import": {"atc": 500},
                    },
                    {
                        "date": "2024-11-03",
                        "he": "2*",
                        "import": {"atc": 450},
                    },
                ]
            }
        }
    )

    rows, _ = await provider.get_intertie_capability(
        date(2024, 11, 3),
        date(2024, 11, 3),
        start_hour_ending=1,
        end_hour_ending=24,
        include_versions=False,
    )

    assert [row.interval_start.fold for row in rows] == [0, 1]
    assert rows[0].interval_start.utcoffset() != rows[1].interval_start.utcoffset()


@pytest.mark.asyncio
async def test_intertie_outages_normalize_documented_shape() -> None:
    provider, _ = _provider(
        {
            "Outages": {
                "Outage": [
                    {
                        "element": "BC line",
                        "affectedIntertieOrFlowgate": ["BC"],
                        "fromInLocalTime": "2024-01-15T00:00:00",
                        "toInLocalTime": "2024-01-16T00:00:00",
                    }
                ]
            }
        }
    )

    rows, _ = await provider.get_intertie_outages(date(2024, 1, 15), date(2024, 1, 16))

    assert rows[0].element == "BC line"
    assert rows[0].affected_interties_or_flowgates == ["BC"]


@pytest.mark.asyncio
async def test_metered_volume_flattens_participant_assets_and_hours() -> None:
    provider, http = _provider(
        {
            "return": {
                "pool_participant_ID": "PP1",
                "asset_list": [
                    {
                        "asset_ID": "ASSET1",
                        "asset_class": "GENERATOR",
                        "metered_volume_list": [
                            {
                                "begin_date_utc": "2024-01-15T07:00:00Z",
                                "metered_volume": "42.5",
                            }
                        ],
                    }
                ],
            }
        }
    )

    rows, _ = await provider.get_metered_volumes(
        date(2024, 1, 15),
        date(2024, 1, 15),
        asset_ids=["ASSET1"],
        pool_participant_ids=[],
    )

    assert rows[0].pool_participant_id == "PP1"
    assert rows[0].metered_volume_mwh == 42.5
    http.get_json.assert_awaited_once_with(
        "meteredvolume-api/v1/meteredvolume/details",
        params={
            "startDate": "2024-01-15",
            "endDate": "2024-01-15",
            "asset_ID": "ASSET1",
        },
    )


@pytest.mark.asyncio
async def test_operating_reserve_offer_control_normalizes_blocks() -> None:
    provider, _ = _provider(
        {
            "Operating Reserve Trade Merit Order": [
                {
                    "begin_datetime_utc": "2024-01-15T07:00:00Z",
                    "operating_reserve_blocks": [
                        {
                            "commodity": "ACTIVE",
                            "product": "SPINNING",
                            "asset_ID": "GEN1",
                            "volume": "12",
                            "active_price": 15,
                            "premium_price": 2,
                            "activation_price": 20,
                            "offer_control": "OC",
                        }
                    ],
                }
            ]
        }
    )

    rows, _ = await provider.get_operating_reserve_offer_control(date(2024, 1, 15))

    assert rows[0].product == "SPINNING"
    assert rows[0].volume_mw == 12
    assert rows[0].activation_price_cad_per_mwh == 20


@pytest.mark.asyncio
async def test_unknown_top_level_shape_is_rejected() -> None:
    provider, _ = _provider("not-an-object")

    with pytest.raises(Exception, match="response shape"):
        await provider.get_generation_capacity(date(2024, 1, 15), date(2024, 1, 15))
