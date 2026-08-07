# SPDX-License-Identifier: MIT
"""Asset registry domain models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from aeso_mcp.models.common import DatasetMetadata, WarningMixin


class AssetsRequest(BaseModel):
    """Filterable asset list request."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str | None = None
    pool_participant_id: str | None = None
    operating_status: str | None = None
    asset_type: str | None = None
    limit: int = Field(default=500, ge=1, le=5_000)


class AssetRecord(BaseModel):
    """One AESO market asset."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    asset_name: str | None = None
    asset_type: str | None = None
    operating_status: str | None = None
    pool_participant_id: str | None = None
    pool_participant_name: str | None = None
    net_to_grid: bool | None = None
    includes_storage: bool | None = None


class AssetsResponse(WarningMixin):
    """AESO asset registry response."""

    assets: list[AssetRecord]
    metadata: DatasetMetadata
    truncated: bool = False
