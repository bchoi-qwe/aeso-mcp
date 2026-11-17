# SPDX-License-Identifier: MIT
"""Small, deterministic MCP tool search; never contacts upstream AESO sources."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from aeso_mcp.models.discovery import ToolSearchMatch, ToolSearchResponse

if TYPE_CHECKING:
    from fastmcp import FastMCP


_STOPWORDS = frozenset(
    {"a", "an", "and", "for", "how", "i", "in", "is", "me", "of", "on", "the", "to", "what"}
)
_SYNONYMS: dict[str, frozenset[str]] = {
    "spike": frozenset({"event", "price"}),
    "accuracy": frozenset({"forecast", "error"}),
    "congestion": frozenset({"constrained", "volume"}),
    "plant": frozenset({"asset", "generation"}),
    "blackout": frozenset({"outage"}),
}


def _tokens(value: str) -> set[str]:
    """Normalize plurals and identifiers so 'reserve prices' matches 'reserve_price'."""
    tokens: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", value.lower()):
        if word in _STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        tokens.add(word)
    return tokens


def rank_tools(query: str, entries: Sequence[Any], *, limit: int) -> ToolSearchResponse:
    """Rank server-registered tools by name and description without network access."""
    words = _tokens(query)
    expanded = set(words)
    for word in words:
        expanded.update(_SYNONYMS.get(word, ()))
    candidates: list[ToolSearchMatch] = []
    for entry in entries:
        if entry.name == "search_aeso_tools":
            continue
        name_tokens = _tokens(entry.name)
        description = entry.description or ""
        description_tokens = _tokens(description)
        name_hits = len(name_tokens & expanded)
        description_hits = len(description_tokens & expanded)
        score = 5 * name_hits + description_hits
        if score <= 0:
            continue
        schema = entry.parameters
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        candidates.append(
            ToolSearchMatch(
                name=entry.name,
                description=description,
                parameters=list(properties),
                required_parameters=list(required),
                relevance_score=score,
            )
        )
    candidates.sort(key=lambda item: (-item.relevance_score, item.name))
    return ToolSearchResponse(
        query=query,
        total_matches=len(candidates),
        matches=candidates[:limit],
    )


def register_discovery_tools(mcp: FastMCP) -> None:
    """Expose a bounded, credential-free search over the live MCP tool surface."""

    @mcp.tool(
        name="search_aeso_tools",
        description=(
            "Search the registered AESO MCP tool catalog by a natural-language task or market "
            "term. Returns ranked tool names, descriptions, and parameter names without querying "
            "AESO. Use this to find the right tool before retrieving data."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def search_aeso_tools(
        query: Annotated[str, Field(min_length=2, max_length=160)],
        limit: Annotated[int, Field(ge=1, le=20)] = 8,
    ) -> ToolSearchResponse:
        return rank_tools(query, await mcp.list_tools(), limit=limit)
