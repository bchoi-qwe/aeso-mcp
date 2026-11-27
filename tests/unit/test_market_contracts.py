# SPDX-License-Identifier: MIT
"""Market contract identity is independent of the calendar date."""

from datetime import UTC, datetime

from aeso_mcp.models.common import DatasetMetadata, MarketContract, source_market_contract
from aeso_mcp.services.provenance import analysis_source_from_response


def test_reviewed_contracts_and_unknowns() -> None:
    assert source_market_contract("Pool Price Report") == MarketContract.LEGACY_POOL
    assert source_market_contract("Energy Merit Order Report") == MarketContract.LEGACY_POOL
    assert source_market_contract("Historical CSD Generation") == MarketContract.UNKNOWN
    assert source_market_contract("REM future unspecified") == MarketContract.UNKNOWN


def test_manifest_retains_market_contract() -> None:
    class Response:
        metadata = DatasetMetadata(
            dataset="Pool Price Report",
            market_contract=MarketContract.LEGACY_POOL,
            retrieved_at=datetime(2026, 11, 27, tzinfo=UTC),
        )

    source = analysis_source_from_response(
        Response(),
        role="focus",
        dataset="Pool Price Report",
        requested_start=None,
        requested_end=None,
    )
    assert source.market_contract == MarketContract.LEGACY_POOL
