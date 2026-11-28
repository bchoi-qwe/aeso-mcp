# SPDX-License-Identifier: MIT
"""Spec preview inspection must never create or validate a REM production endpoint."""

import json
from pathlib import Path

import pytest

from scripts.review_rem_openapi import review_spec, validate_inventory


def test_official_rem_inventory_is_explicitly_preproduction() -> None:
    payload = json.loads(Path("docs/rem-endpoint-inventory.json").read_text())
    validate_inventory(payload)
    assert payload["current_api"]["rem"] is False
    assert all(not entry["production_live_verified"] for entry in payload["rem_endpoints"])


def test_preview_openapi_is_offline_and_reports_methods_only(tmp_path: Path) -> None:
    path = tmp_path / "preview.json"
    path.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"version": "preview-1"},
                "servers": [{"url": "https://sandbox.example.net/api?secret=sensitive"}],
                "paths": {
                    "/offers": {"post": {"responses": {"200": {}}}},
                    "/prices": {"get": {"responses": {"200": {}}}},
                },
            }
        )
    )
    result = review_spec(path)
    assert result["path_count"] == 2
    assert result["operations_by_method"] == {"get": 1, "post": 1}
    assert result["declared_https_server_hosts"] == ["sandbox.example.net"]
    assert not result["rem_production_endpoint_verified"]
    assert "sensitive" not in json.dumps(result)


def test_swagger_preview_and_invalid_documents(tmp_path: Path) -> None:
    path = tmp_path / "swagger.json"
    path.write_text(json.dumps({"swagger": "2.0", "host": "sandbox.example.net", "paths": {}}))
    assert review_spec(path)["specification_format"] == "Swagger 2"
    path.write_text(json.dumps({"openapi": "3.1.0", "paths": {"bad": {}}}))
    with pytest.raises(ValueError, match="Invalid API path"):
        review_spec(path)


def test_inventory_cannot_claim_production_or_invent_url() -> None:
    entry = {"stage": "published_preview", "production_base_url": "https://example.net"}
    with pytest.raises(ValueError, match="production endpoint"):
        validate_inventory({"rem_endpoints": [entry]})
    entry["production_base_url"] = None
    entry["production_live_verified"] = True
    with pytest.raises(ValueError, match="production endpoint"):
        validate_inventory({"rem_endpoints": [entry]})
