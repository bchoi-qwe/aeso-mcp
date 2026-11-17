# SPDX-License-Identifier: MIT
"""Typed tool-discovery results for agent clients."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ToolSearchMatch(BaseModel):
    """One relevant registered MCP tool."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: list[str] = Field(default_factory=list)
    required_parameters: list[str] = Field(default_factory=list)
    relevance_score: int = Field(ge=1)


class ToolSearchResponse(BaseModel):
    """Bounded matches from the actual installed server tool catalog."""

    model_config = ConfigDict(extra="forbid")

    query: str
    total_matches: int
    matches: list[ToolSearchMatch]
    ranking_method: str = "deterministic lexical relevance"
