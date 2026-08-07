# SPDX-License-Identifier: MIT
"""Console entrypoint for the AESO MCP server."""

from __future__ import annotations

import argparse
import logging
import sys


def _configure_logging(level: str) -> None:
    """Send logs to stderr so stdio MCP transport remains clean."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
        force=True,
    )


def main(argv: list[str] | None = None) -> None:
    """Run the AESO MCP server."""
    parser = argparse.ArgumentParser(
        prog="aeso-mcp",
        description="AESO Model Context Protocol server for Alberta electricity market data.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="MCP transport (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP bind host (http transport only).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP bind port (http transport only).",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override AESO_MCP_LOG_LEVEL for this process.",
    )
    args = parser.parse_args(argv)

    # Import after parsing so `--help` works without requiring credentials.
    from aeso_mcp.config import get_settings
    from aeso_mcp.mcp.server import create_mcp_server

    settings = get_settings()
    log_level = args.log_level or settings.log_level
    _configure_logging(log_level)

    mcp = create_mcp_server(settings)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
