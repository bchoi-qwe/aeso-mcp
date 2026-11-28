#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inspect a user-supplied AESO REM API JSON without probing any endpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})
MAX_SPEC_BYTES = 8 * 1024 * 1024


def review_spec(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_SPEC_BYTES:
        raise ValueError("OpenAPI JSON exceeds the offline review limit")
    raw = path.read_bytes()
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("API specification must be a JSON object")
    version = document.get("openapi")
    if isinstance(version, str) and version.startswith("3."):
        format_name = "OpenAPI 3"
        listed_servers = document.get("servers", [])
        if not isinstance(listed_servers, list):
            raise ValueError("Invalid OpenAPI server list")
        server_values = [server.get("url") for server in listed_servers if isinstance(server, dict)]
    elif document.get("swagger") == "2.0":
        format_name = "Swagger 2"
        host = document.get("host")
        schemes = document.get("schemes") or ["https"]
        if not isinstance(schemes, list):
            raise ValueError("Invalid Swagger schemes")
        server_values = [
            f"{scheme}://{host}" for scheme in schemes if isinstance(scheme, str) and host
        ]
    else:
        raise ValueError("The provided JSON is not a recognized OpenAPI or Swagger specification")
    paths = document.get("paths")
    if not isinstance(paths, dict) or len(paths) > 2000:
        raise ValueError("Missing or excessively large OpenAPI paths mapping")
    counts: Counter[str] = Counter()
    for name, definitions in paths.items():
        if (
            not isinstance(name, str)
            or not name.startswith("/")
            or not isinstance(definitions, dict)
        ):
            raise ValueError("Invalid API path definition")
        for method in definitions:
            if isinstance(method, str) and method.lower() in METHODS:
                counts[method.lower()] += 1
    server_hosts = set()
    unverified_servers = 0
    for server in server_values:
        if not isinstance(server, str) or "{" in server or "}" in server:
            unverified_servers += 1
            continue
        parsed = urlsplit(server)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            unverified_servers += 1
            continue
        server_hosts.add(parsed.hostname.lower())
    return {
        "specification_sha256": hashlib.sha256(raw).hexdigest(),
        "specification_format": format_name,
        "declared_version": version if format_name == "OpenAPI 3" else "2.0",
        "declared_api_version": (
            document.get("info", {}).get("version")
            if isinstance(document.get("info"), dict)
            else None
        ),
        "path_count": len(paths),
        "operations_by_method": dict(sorted(counts.items())),
        "declared_https_server_hosts": sorted(server_hosts),
        "unverifiable_server_declarations": unverified_servers,
        "review_scope": "OFFLINE PREVIEW ONLY; no network requests or production verification",
        "rem_production_endpoint_verified": False,
    }


def validate_inventory(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("rem_endpoints"), list) or not payload["rem_endpoints"]:
        raise ValueError("REM inventory must include endpoint candidates")
    for entry in payload["rem_endpoints"]:
        if not isinstance(entry, dict) or entry.get("stage") not in {
            "engagement",
            "published_preview",
            "sandbox_announced",
        }:
            raise ValueError("No REM endpoint has verified production status")
        if entry.get("production_base_url") is not None or entry.get("production_live_verified"):
            raise ValueError("REM production endpoint is not verified by current evidence")
        if entry.get("sandbox_base_url") is not None and entry.get("stage") != "sandbox_announced":
            raise ValueError("Sandbox URLs require an explicit sandbox stage")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=Path("docs/rem-endpoint-inventory.json"))
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.inventory.read_text(encoding="utf-8"))
    validate_inventory(payload)
    result = {"inventory_reviewed_on": payload["reviewed_on"], "rem_production_verified": False}
    if args.spec is not None:
        result["preview_spec_review"] = review_spec(args.spec)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
