# SPDX-License-Identifier: MIT
"""Unit tests for configuration and error hygiene."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aeso_mcp.config import Settings, clear_settings_cache, get_settings
from aeso_mcp.errors import ConfigurationError


def test_missing_api_key_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AESO_API_KEY", raising=False)
    clear_settings_cache()
    with pytest.raises(ConfigurationError, match="AESO_API_KEY"):
        get_settings()


def test_settings_do_not_expose_secret_in_repr() -> None:
    settings = Settings(aeso_api_key="super-secret-key-value")  # type: ignore[arg-type]
    text = repr(settings)
    assert "super-secret-key-value" not in text
    assert settings.api_key_value == "super-secret-key-value"


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"aeso_api_key": "x", "log_level": "VERBOSE"})
