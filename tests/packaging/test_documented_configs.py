# SPDX-License-Identifier: MIT
"""Keep checked-in MCP client configuration templates syntactically valid."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_codex_mcp_config_template() -> None:
    config = tomllib.loads((ROOT / "docs/configs/aeso.codex.toml").read_text(encoding="utf-8"))
    server = config["mcp_servers"]["aeso"]
    assert server["command"] == "uvx"
    assert server["args"][-1] == "aeso-mcp"
    assert server["env_vars"] == ["AESO_API_KEY"]


def test_json_mcp_config_template() -> None:
    config = json.loads((ROOT / "docs/configs/aeso.mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["aeso"]
    assert server["command"] == "uvx"
    assert server["args"][-1] == "aeso-mcp"
    assert set(server["env"]) == {"AESO_API_KEY"}
