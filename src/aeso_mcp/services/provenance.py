# SPDX-License-Identifier: MIT
"""Canonical construction of research reproducibility manifests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import JsonValue

from aeso_mcp.models.common import DataCompleteness, FinalityStatus
from aeso_mcp.models.provenance import AnalysisManifest, AnalysisSource, AnalysisSourceRole
from aeso_mcp.timeutil import utc_now


def analysis_source_from_response(
    response: object,
    *,
    role: AnalysisSourceRole,
    dataset: str,
    requested_start: datetime | None,
    requested_end: datetime | None,
) -> AnalysisSource:
    """Normalize a typed response's metadata without inventing absent provenance."""

    metadata = getattr(response, "metadata", None)
    extra = getattr(metadata, "extra", {}) if metadata is not None else {}
    if not isinstance(extra, Mapping):
        extra = {}
    provider = _enum_value(getattr(metadata, "provider", "unknown"))
    finality = _coerce_finality(getattr(metadata, "finality", FinalityStatus.UNKNOWN))
    completeness = _coerce_completeness(getattr(metadata, "completeness", DataCompleteness.UNKNOWN))
    observation_count = getattr(metadata, "observation_count", None)
    if not isinstance(observation_count, int):
        observation_count = _response_count(response)
    return AnalysisSource(
        role=role,
        dataset=str(getattr(metadata, "dataset", dataset)),
        source_product=_optional_text(getattr(metadata, "source_product", None)),
        provider=provider,
        source_version=_first_text(
            getattr(metadata, "api_version", None),
            extra.get("source_version"),
            extra.get("schema_version"),
        ),
        source_file_id=_first_text(extra.get("source_file_id"), extra.get("file_id")),
        source_file_name=_first_text(extra.get("source_file_name"), extra.get("file_name")),
        publication_time=getattr(metadata, "publication_time", None),
        retrieved_at=getattr(metadata, "retrieved_at", None),
        requested_start=requested_start,
        requested_end=requested_end,
        observation_count=observation_count,
        finality=finality,
        completeness=completeness,
        source_hash=_first_text(extra.get("source_hash"), extra.get("content_hash")),
    )


def build_analysis_manifest(
    *,
    methodology_version: str,
    sources: Sequence[AnalysisSource],
    parameters: Mapping[str, JsonValue],
    warnings: Sequence[str] = (),
    generated_at: datetime | None = None,
) -> AnalysisManifest:
    """Build a stable analysis identity independent of fetch completion order."""

    normalized_sources = sorted(sources, key=_source_sort_key)
    normalized_parameters = dict(sorted(parameters.items()))
    normalized_warnings = sorted(set(warnings))
    identity_payload = {
        "methodology_version": methodology_version,
        "sources": [source.model_dump(mode="json") for source in normalized_sources],
        "parameters": normalized_parameters,
        "warnings": normalized_warnings,
    }
    canonical = json.dumps(identity_payload, sort_keys=True, separators=(",", ":"))
    analysis_id = f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    return AnalysisManifest(
        analysis_id=analysis_id,
        methodology_version=methodology_version,
        generated_at=generated_at or utc_now(),
        sources=normalized_sources,
        parameters=normalized_parameters,
        warnings=normalized_warnings,
    )


def _source_sort_key(source: AnalysisSource) -> tuple[str, ...]:
    return (
        source.role,
        source.dataset,
        source.source_product or "",
        source.source_version or "",
        source.source_file_id or "",
        source.source_file_name or "",
        source.requested_start.isoformat() if source.requested_start is not None else "",
        source.requested_end.isoformat() if source.requested_end is not None else "",
        source.publication_time.isoformat() if source.publication_time is not None else "",
        source.retrieved_at.isoformat() if source.retrieved_at is not None else "",
        source.source_hash or "",
    )


def _response_count(response: object) -> int | None:
    for attribute in ("intervals", "records", "outages", "blocks", "directives", "results"):
        value = getattr(response, attribute, None)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return len(value)
    return None


def _enum_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _first_text(*values: object) -> str | None:
    for value in values:
        if value is not None:
            return str(value)
    return None


def _coerce_finality(value: Any) -> FinalityStatus:
    try:
        return FinalityStatus(_enum_value(value))
    except ValueError:
        return FinalityStatus.UNKNOWN


def _coerce_completeness(value: Any) -> DataCompleteness:
    try:
        return DataCompleteness(_enum_value(value))
    except ValueError:
        return DataCompleteness.UNKNOWN


__all__ = ["analysis_source_from_response", "build_analysis_manifest"]
