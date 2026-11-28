# SPDX-License-Identifier: MIT
"""Official web page changes require review, never automatic endpoint activation."""

from scripts.audit_rem_public_sources import inspect_page


def test_public_rem_monitor_does_not_claim_api_production() -> None:
    page = b"<html><body>Soft Launch. MUI API Specification. AESO_MUI_API.json</body></html>"
    result = inspect_page(page, ("Soft Launch", "MUI API Specification", "AESO_MUI_API.json"))
    assert result["source_changed_or_inaccessible"] is False
    assert result["production_endpoint_verified"] is False
    changed = inspect_page(page, ("Soft Launch", "Production endpoint is verified"))
    assert changed["source_changed_or_inaccessible"] is True


def test_rem_monitor_ignores_script_content() -> None:
    page = b"<script>Soft Launch MUI API Specification</script><p>Actual text</p>"
    result = inspect_page(page, ("Soft Launch",))
    assert result["source_changed_or_inaccessible"] is True
