#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate or verify the Markdown catalog from the actual MCP surface."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pydantic import SecretStr

from aeso_mcp.app import build_container
from aeso_mcp.config import Settings
from aeso_mcp.mcp.server import create_mcp_server

DEFAULT_OUTPUT = Path("docs/generated/mcp-catalog.md")


async def render_catalog() -> str:
    """Render tools, prompts, and resources without making upstream requests."""
    settings = Settings(aeso_api_key=SecretStr("catalog-generation-key"))
    container = build_container(settings)
    try:
        mcp = create_mcp_server(settings, container)
        tools = sorted(await mcp.list_tools(), key=lambda item: item.name)
        prompts = sorted(await mcp.list_prompts(), key=lambda item: item.name)
        resources = sorted(await mcp.list_resources(), key=lambda item: str(item.uri))
    finally:
        await container.aclose()

    lines = [
        "# Generated MCP catalog",
        "",
        "This file is generated from the registered FastMCP surface. Do not edit it by hand.",
        "",
        f"## Tools ({len(tools)})",
        "",
        "| Tool | Inputs | Description |",
        "| --- | --- | --- |",
    ]
    for tool in tools:
        properties = tool.parameters.get("properties", {})
        inputs = ", ".join(f"`{name}`" for name in properties) or "—"
        description = " ".join((tool.description or "").split()).replace("|", "\\|")
        lines.append(f"| `{tool.name}` | {inputs} | {description} |")
    dataset_tools = [tool for tool in tools if tool.name.startswith("get_")]
    lines.extend(
        [
            "",
            "## Dataset and retrieval coverage",
            "",
            f"This table is generated from {len(dataset_tools)} registered retrieval tools and "
            "their live descriptions.",
            "",
            "| Tool | Registered data contract |",
            "| --- | --- |",
            *[
                "| `"
                + tool.name
                + "` | "
                + " ".join((tool.description or "").split()).replace("|", "\\|")
                + " |"
                for tool in dataset_tools
            ],
            "",
            f"## Prompts ({len(prompts)})",
            "",
            *[f"- `{prompt.name}`" for prompt in prompts],
            "",
            f"## Resources ({len(resources)})",
            "",
            *[f"- `{resource.uri}`" for resource in resources],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if the catalog is stale.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rendered = asyncio.run(render_catalog())
    if args.check:
        existing = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if existing != rendered:
            raise SystemExit(
                f"{args.output} is stale; run `uv run python scripts/generate_catalog.py`."
            )
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
