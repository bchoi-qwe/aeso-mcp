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
_CANONICAL_VERSION = re.compile(
    r"^(?:\d+!)?\d+(?:\.\d+)*(?P<pre>a\d*|b\d*|rc\d*)?"
    r"(?:\.post\d*)?(?P<dev>\.dev\d*)?(?:\+[-a-z0-9.]+)?$",
    re.IGNORECASE,
)


def is_prerelease(version: str) -> bool:
    """Return whether a lockfile version uses a PEP 440 prerelease marker."""
    match = _CANONICAL_VERSION.fullmatch(version)
    return match is not None and (match.group("pre") is not None or match.group("dev") is not None)


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
        if is_prerelease(version):
            unexpected.append((name, version))
    return tuple(sorted(unexpected))


def check_lock(lock_path: Path = LOCK_PATH) -> tuple[tuple[str, str], ...]:
    """Load a lockfile and return unexpected prerelease package identities."""
    lock_data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return find_unexpected_prereleases(lock_data)


def main(argv: Sequence[str] | None = None) -> int:
    """Check the lockfile and resolver mode, returning a CI-friendly status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    args = parser.parse_args(argv)

    unexpected = check_lock(args.lock)
    if unexpected:
        packages = ", ".join(f"{name}=={version}" for name, version in unexpected)
        sys.stderr.write(f"Prerelease packages are not allowed in {args.lock}: {packages}\n")
        return 1

    sys.stdout.write(
        f"Lock prerelease policy passed; no prerelease packages found in {args.lock}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
