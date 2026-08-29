#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reject unexpected prerelease packages from the checked-in uv lockfile."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "uv.lock"
PYPROJECT_PATH = ROOT / "pyproject.toml"

# FastMCP 4.0.0b3 is the one intentionally prerelease dependency. Its slim
# distribution is a required transitive dependency of the exact FastMCP pin.
ALLOWED_PRERELEASES = frozenset(
    {
        ("fastmcp", "4.0.0b3"),
        ("fastmcp-slim", "4.0.0b3"),
    }
)
_PRERELEASE_MARKER = re.compile(r"(?:\.dev\d*|(?:a|b|rc)\d+)(?:$|[.+])", re.IGNORECASE)


def is_prerelease(version: str) -> bool:
    """Return whether a lockfile version uses a PEP 440 prerelease marker."""
    return _PRERELEASE_MARKER.search(version) is not None


def find_unexpected_prereleases(lock_data: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Return sorted prerelease package identities outside the explicit allowlist."""
    packages = lock_data.get("package")
    if not isinstance(packages, list):
        raise ValueError("uv.lock must contain a package array")

    unexpected: list[tuple[str, str]] = []
    for package in packages:
        if not isinstance(package, Mapping):
            raise ValueError("uv.lock package entries must be tables")
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError("uv.lock package entries must declare string name and version")
        identity = (name, version)
        if is_prerelease(version) and identity not in ALLOWED_PRERELEASES:
            unexpected.append(identity)
    return tuple(sorted(unexpected))


def check_lock(lock_path: Path = LOCK_PATH) -> tuple[tuple[str, str], ...]:
    """Load a lockfile and return unexpected prerelease package identities."""
    lock_data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return find_unexpected_prereleases(lock_data)


def check_pyproject(pyproject_path: Path = PYPROJECT_PATH) -> str | None:
    """Return a policy error when uv is configured to allow prereleases globally."""
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    prerelease_mode = pyproject.get("tool", {}).get("uv", {}).get("prerelease")
    if prerelease_mode != "if-necessary":
        return (
            "pyproject.toml [tool.uv].prerelease must be 'if-necessary' to keep "
            f"prereleases constrained; found {prerelease_mode!r}"
        )
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Check the lockfile and resolver mode, returning a CI-friendly status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--pyproject", type=Path, default=PYPROJECT_PATH)
    args = parser.parse_args(argv)

    policy_error = check_pyproject(args.pyproject)
    unexpected = check_lock(args.lock)
    if policy_error is not None:
        sys.stderr.write(f"{policy_error}\n")
    if unexpected:
        packages = ", ".join(f"{name}=={version}" for name, version in unexpected)
        sys.stderr.write(f"Unexpected prerelease packages in {args.lock}: {packages}\n")
    if policy_error is not None or unexpected:
        return 1

    allowed = ", ".join(f"{name}=={version}" for name, version in sorted(ALLOWED_PRERELEASES))
    sys.stdout.write(f"Lock prerelease policy passed; allowed: {allowed}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
