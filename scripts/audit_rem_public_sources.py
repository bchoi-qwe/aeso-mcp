#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Monitor official public REM milestones; never infer production API availability."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx

MAX_PAGE_BYTES = 2 * 1024 * 1024
PAGES = {
    "implementation": {
        "url": "https://aesoengage.aeso.ca/rem-it-systems-implementation",
        "expected": ("AESO_MUI_API.json", "MUI API Specification", "Soft Launch"),
    },
    "schedule": {
        "url": "https://www.aeso.ca/transition/it-systems/",
        "expected": ("Soft Launch", "System Trials", "Q2 2028"),
    },
}
ALLOWED_HOSTS = frozenset({"aesoengage.aeso.ca", "www.aeso.ca", "aeso.ca"})


class _ReadableText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.suppressed:
            self.suppressed -= 1

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


def inspect_page(content: bytes, required: tuple[str, ...]) -> dict[str, object]:
    if len(content) > MAX_PAGE_BYTES:
        raise ValueError("Official REM page exceeds the configured size limit")
    text = _ReadableText()
    text.feed(content.decode("utf-8", errors="replace"))
    normalized = re.sub(r"\s+", " ", " ".join(text.parts)).casefold()
    present = {term: term.casefold() in normalized for term in required}
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "required_phrases": present,
        "source_changed_or_inaccessible": not all(present.values()),
        "production_endpoint_verified": False,
    }


def audit() -> dict[str, object]:
    results: dict[str, object] = {}
    with httpx.Client(
        timeout=httpx.Timeout(15.0),
        follow_redirects=False,
        headers={"User-Agent": "aeso-mcp-rem-readiness-monitor/1.0"},
    ) as client:
        for name, spec in PAGES.items():
            url = str(spec["url"])
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
                raise ValueError("REM monitor URL must be one of the reviewed official pages")
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_PAGE_BYTES:
                        raise ValueError("Official REM page exceeds the size limit")
            results[name] = {
                "url": url,
                **inspect_page(bytes(content), spec["expected"]),
            }
    return {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "status": "PUBLIC_SOURCE_MONITOR_ONLY",
        "pages": results,
        "rem_production_endpoints_live_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit()
    output = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    sys.stdout.write(output)
    pages = result["pages"]
    if any(page["source_changed_or_inaccessible"] for page in pages.values()):
        raise SystemExit("Official REM status phrases changed: review sources manually")


if __name__ == "__main__":
    main()
