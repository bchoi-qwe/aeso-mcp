# SPDX-License-Identifier: MIT
"""MCP resource registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aeso_mcp.mcp.resources.capabilities import register_capabilities_resource
from aeso_mcp.mcp.resources.datasets import register_resources as register_dataset_resources
from aeso_mcp.mcp.resources.methodology import register_methodology_resources

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = ["register_resources"]


def register_resources(mcp: FastMCP) -> None:
    """Register catalog, methodology, and capability resources."""
    register_dataset_resources(mcp)
    register_methodology_resources(mcp)
    register_capabilities_resource(mcp)
