# SPDX-License-Identifier: MIT
"""Reproducibility contracts for deterministic multi-source analyses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aeso_mcp.models.common import DataCompleteness, FinalityStatus

AnalysisSourceRole = Literal["focus", "baseline", "context"]

# Pydantic's JsonValue currently emits `{}` in JSON Schema. This recursive alias
# accurately constrains the public manifest to JSON values and keeps the MCP
# output schema machine-readable instead of advertising unconstrained `Any`.
type JSONValue = bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None


class AnalysisSource(BaseModel):
    """One exact upstream input represented in an analysis manifest."""

    model_config = ConfigDict(extra="forbid")

    role: AnalysisSourceRole
    dataset: str
    source_product: str | None = None
    provider: str
    source_version: str | None = None
    source_file_id: str | None = None
    source_file_name: str | None = None
    publication_time: datetime | None = None
    retrieved_at: datetime | None = None
    requested_start: datetime | None = None
    requested_end: datetime | None = None
    observation_count: int | None = Field(default=None, ge=0)
    finality: FinalityStatus = FinalityStatus.UNKNOWN
    completeness: DataCompleteness = DataCompleteness.UNKNOWN
    source_hash: str | None = None


class AnalysisManifest(BaseModel):
    """Canonical inputs and parameters needed to reproduce an analysis."""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(
        description="SHA-256 identity of methodology, sources, and parameters."
    )
    methodology_version: str
    generated_at: datetime
    sources: list[AnalysisSource]
    parameters: dict[str, JSONValue]
    warnings: list[str] = Field(default_factory=list)


__all__ = ["AnalysisManifest", "AnalysisSource", "AnalysisSourceRole"]
