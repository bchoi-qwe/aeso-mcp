#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify that release metadata uses one package version everywhere it is declared."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    """Check all source, package, registry, and citation versions for one value."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = pyproject["project"]["version"]
    registry = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    registry_version = registry["version"]

    source = (ROOT / "src/aeso_mcp/__init__.py").read_text(encoding="utf-8")
    source_match = re.search(
        r'^__version__\s*=\s*["\'](?P<version>[^"\']+)["\']\s*$',
        source,
        re.MULTILINE,
    )
    if source_match is None:
        raise SystemExit("src/aeso_mcp/__init__.py does not declare __version__")
    source_version = source_match.group("version")

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*(?P<version>[^\s#]+)\s*$", citation, re.MULTILINE)
    if match is None:
        raise SystemExit("CITATION.cff does not declare a version")
    citation_version = match.group("version").strip("\"'")

    versions = {
        "src/aeso_mcp/__init__.py": source_version,
        "pyproject.toml": package_version,
        "server.json": registry_version,
        "CITATION.cff": citation_version,
    }
    versions.update(
        {
            f"server.json packages[{index}]": package["version"]
            for index, package in enumerate(registry.get("packages", []))
        }
    )
    if len(set(versions.values())) != 1:
        details = ", ".join(f"{path}={version}" for path, version in versions.items())
        raise SystemExit(f"Release version mismatch: {details}")

    sys.stdout.write(f"Release metadata version: {package_version}\n")


if __name__ == "__main__":
    main()
