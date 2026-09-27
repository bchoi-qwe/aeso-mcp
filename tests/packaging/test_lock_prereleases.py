# SPDX-License-Identifier: MIT
"""Tests for the checked-in dependency prerelease policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.packaging.check_lock_prereleases import (
    check_lock,
    find_unexpected_prereleases,
    is_prerelease,
    main,
)


@pytest.mark.parametrize(
    "version",
    [
        "1.2.3.dev4",
        "1.2.3.dev",
        "1.2.3a",
        "1.2.3a1",
        "1.2.3b2",
        "1.2.3rc1",
        "1.2.3b2+local",
        "1.2.3.post1.dev4",
    ],
)
def test_prerelease_markers_are_detected(version: str) -> None:
    assert is_prerelease(version)


def test_stable_versions_are_not_prereleases() -> None:
    assert not is_prerelease("1.2.3")
    assert not is_prerelease("1.2.3.post1")
    assert not is_prerelease("1.2.3+alpha1")


def test_all_prereleases_are_rejected_including_former_fastmcp_allowlist() -> None:
    lock_data = {
        "package": [
            {"name": "fastmcp", "version": "4.0.0b3"},
            {"name": "fastmcp-slim", "version": "4.0.0b3"},
        ]
    }

    assert find_unexpected_prereleases(lock_data) == (
        ("fastmcp", "4.0.0b3"),
        ("fastmcp-slim", "4.0.0b3"),
    )


def test_unrelated_prereleases_are_rejected() -> None:
    lock_data = {
        "package": [
            {"name": "pydantic", "version": "2.14.0b1"},
            {"name": "duckdb", "version": "1.6.0.dev343"},
            {"name": "cyclopts", "version": "5.0.0b1"},
        ]
    }

    assert find_unexpected_prereleases(lock_data) == (
        ("cyclopts", "5.0.0b1"),
        ("duckdb", "1.6.0.dev343"),
        ("pydantic", "2.14.0b1"),
    )


def test_policy_passes_for_checked_in_lock() -> None:
    assert check_lock() == ()
    assert main([]) == 0


def test_policy_rejects_any_locked_prerelease(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        "[[package]]\nname = 'fastmcp'\nversion = '4.0.0b3'\n",
        encoding="utf-8",
    )

    assert main(["--lock", str(lock)]) == 1
    assert "Prerelease packages are not allowed" in capsys.readouterr().err
