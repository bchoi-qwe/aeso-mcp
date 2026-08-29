# SPDX-License-Identifier: MIT
"""Exercise the installed console entrypoint without starting a server."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

import aeso_mcp.config
import aeso_mcp.http_runtime
import aeso_mcp.mcp.server
from aeso_mcp import __main__


class _FakeMcp:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] | None = None

    def run(self, **kwargs: Any) -> None:
        self.run_kwargs = kwargs


def test_help_does_not_require_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        __main__.main(["--help"])

    assert raised.value.code == 0
    assert "AESO Model Context Protocol server" in capsys.readouterr().out


def test_stdio_entrypoint_builds_server_and_keeps_stdio_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(log_level="INFO")
    fake_mcp = _FakeMcp()
    monkeypatch.setattr(aeso_mcp.config, "get_settings", lambda: settings)
    monkeypatch.setattr(aeso_mcp.mcp.server, "create_mcp_server", lambda value: fake_mcp)

    __main__.main(["--transport", "stdio", "--log-level", "DEBUG"])

    assert fake_mcp.run_kwargs == {"transport": "stdio", "show_banner": False}


def test_stdio_entrypoint_uses_configured_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(log_level="ERROR")
    fake_mcp = _FakeMcp()
    monkeypatch.setattr(aeso_mcp.config, "get_settings", lambda: settings)
    monkeypatch.setattr(aeso_mcp.mcp.server, "create_mcp_server", lambda value: fake_mcp)

    __main__.main(["--transport", "stdio"])

    assert logging.getLogger().level == logging.ERROR
    assert fake_mcp.run_kwargs == {"transport": "stdio", "show_banner": False}


def test_http_entrypoint_passes_runtime_controls_to_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(log_level="WARNING")
    fake_mcp = _FakeMcp()
    runtime = SimpleNamespace(
        middleware=["middleware"],
        host_origin_protection=True,
        allowed_hosts=["localhost:9000"],
        allowed_origins=["http://localhost:9000"],
    )
    runtime_calls: list[tuple[object, object, str]] = []

    monkeypatch.setattr(aeso_mcp.config, "get_settings", lambda: settings)
    monkeypatch.setattr(aeso_mcp.mcp.server, "create_mcp_server", lambda value: fake_mcp)

    def fake_build_runtime(mcp: object, value: object, *, host: str) -> object:
        runtime_calls.append((mcp, value, host))
        return runtime

    monkeypatch.setattr(aeso_mcp.http_runtime, "build_http_runtime", fake_build_runtime)
    __main__.main(
        [
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
            "--log-level",
            "WARNING",
        ]
    )

    assert runtime_calls == [(fake_mcp, settings, "127.0.0.1")]
    assert fake_mcp.run_kwargs == {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 9000,
        "show_banner": False,
        "middleware": ["middleware"],
        "host_origin_protection": True,
        "allowed_hosts": ["localhost:9000"],
        "allowed_origins": ["http://localhost:9000"],
    }
