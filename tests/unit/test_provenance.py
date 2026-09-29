# SPDX-License-Identifier: MIT
"""Research provenance manifest behavior."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from aeso_mcp.models.common import DataCompleteness, FinalityStatus
from aeso_mcp.models.provenance import AnalysisManifest, AnalysisSource
from aeso_mcp.services.provenance import build_analysis_manifest


def _source(dataset: str, retrieved_at: datetime) -> AnalysisSource:
    return AnalysisSource(
        role="focus",
        dataset=dataset,
        source_product=f"AESO {dataset}",
        provider="aeso_apim",
        source_version="v1",
        retrieved_at=retrieved_at,
        requested_start=datetime(2026, 8, 1, tzinfo=UTC),
        requested_end=datetime(2026, 8, 2, tzinfo=UTC),
        observation_count=24,
        finality=FinalityStatus.FINAL,
        completeness=DataCompleteness.COMPLETE,
        source_hash=f"sha256:{dataset}",
    )


def test_analysis_identity_is_stable_across_source_completion_order() -> None:
    retrieved_at = datetime(2026, 8, 3, tzinfo=UTC)
    sources = [_source("Pool Price", retrieved_at), _source("AIL", retrieved_at)]
    parameters = {"threshold": 100.0, "include_forecasts": True}

    first = build_analysis_manifest(
        methodology_version="market-event-v1",
        sources=sources,
        parameters=parameters,
        generated_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    second = build_analysis_manifest(
        methodology_version="market-event-v1",
        sources=list(reversed(sources)),
        parameters=dict(reversed(list(parameters.items()))),
        generated_at=datetime(2026, 8, 5, tzinfo=UTC),
    )

    assert first.analysis_id == second.analysis_id
    assert [source.dataset for source in first.sources] == ["AIL", "Pool Price"]
    assert first.generated_at != second.generated_at


def test_analysis_identity_changes_with_research_parameters() -> None:
    source = _source("Pool Price", datetime(2026, 8, 3, tzinfo=UTC))
    first = build_analysis_manifest(
        methodology_version="market-event-v1",
        sources=[source],
        parameters={"threshold": 100.0},
    )
    second = build_analysis_manifest(
        methodology_version="market-event-v1",
        sources=[source],
        parameters={"threshold": 250.0},
    )

    assert first.analysis_id != second.analysis_id


def test_analysis_identity_includes_normalized_degradation_warnings() -> None:
    source = _source("Pool Price", datetime(2026, 8, 3, tzinfo=UTC))
    first = build_analysis_manifest(
        methodology_version="market-event-v1",
        sources=[source],
        parameters={"threshold": 100.0},
        warnings=["missing reserve history", "partial outage coverage"],
    )
    reordered = build_analysis_manifest(
        methodology_version="market-event-v1",
        sources=[source],
        parameters={"threshold": 100.0},
        warnings=[
            "partial outage coverage",
            "missing reserve history",
            "partial outage coverage",
        ],
    )
    complete = build_analysis_manifest(
        methodology_version="market-event-v1",
        sources=[source],
        parameters={"threshold": 100.0},
    )

    assert first.analysis_id == reordered.analysis_id
    assert first.warnings == ["missing reserve history", "partial outage coverage"]
    assert first.analysis_id != complete.analysis_id


def test_analysis_identity_totally_orders_sources_with_matching_labels() -> None:
    source = _source("Pool Price", datetime(2026, 8, 3, tzinfo=UTC))
    other = source.model_copy(update={"observation_count": 12})
    first = build_analysis_manifest(
        methodology_version="v1", sources=[source, other], parameters={}
    )
    second = build_analysis_manifest(
        methodology_version="v1", sources=[other, source], parameters={}
    )
    assert first.analysis_id == second.analysis_id


def test_analysis_manifest_parameters_use_recursive_json_schema() -> None:
    schema = AnalysisManifest.model_json_schema()
    parameters = schema["properties"]["parameters"]
    value_ref = parameters["additionalProperties"]["$ref"]
    value_name = value_ref.rsplit("/", maxsplit=1)[-1]
    value_schema = schema["$defs"][value_name]

    assert parameters["type"] == "object"
    assert {choice.get("type") for choice in value_schema["anyOf"]} == {
        "boolean",
        "integer",
        "number",
        "string",
        "array",
        "object",
        "null",
    }
    recursive_ref = {"$ref": value_ref}
    assert (
        next(choice for choice in value_schema["anyOf"] if choice.get("type") == "array")["items"]
        == recursive_ref
    )
    assert (
        next(choice for choice in value_schema["anyOf"] if choice.get("type") == "object")[
            "additionalProperties"
        ]
        == recursive_ref
    )

    manifest = AnalysisManifest(
        analysis_id="sha256:test",
        methodology_version="v1",
        generated_at=datetime(2026, 8, 4, tzinfo=UTC),
        sources=[],
        parameters={"nested": {"values": [1, True, None, "text"]}},
    )
    assert manifest.parameters["nested"] == {"values": [1, True, None, "text"]}
    with pytest.raises(ValidationError):
        AnalysisManifest(
            analysis_id="sha256:test",
            methodology_version="v1",
            generated_at=datetime(2026, 8, 4, tzinfo=UTC),
            sources=[],
            parameters={"unsupported": object()},
        )


def test_analysis_source_hash_canonicalizes_date_valued_observations() -> None:
    from aeso_mcp.models.common import DatasetMetadata, ProviderName
    from aeso_mcp.models.operations import PageInfo
    from aeso_mcp.models.reports import TmrReferencePrice, TmrReferencePriceResponse
    from aeso_mcp.services.provenance import analysis_source_from_response

    retrieved_at = datetime(2026, 8, 3, tzinfo=UTC)
    response = TmrReferencePriceResponse(
        records=[
            TmrReferencePrice(effective_date=date(2026, 8, 1), reference_price_cad_per_mwh=42.0)
        ],
        page=PageInfo(offset=0, limit=1, returned=1, total=1),
        metadata=DatasetMetadata(
            dataset="TMR reference price",
            provider=ProviderName.AESO_PUBLIC_REPORT,
            retrieved_at=retrieved_at,
        ),
    )

    source = analysis_source_from_response(
        response,
        role="focus",
        dataset="TMR reference price",
        requested_start=None,
        requested_end=None,
    )

    assert source.source_hash is not None
    assert source.source_hash.startswith("sha256:")


def test_analysis_source_hash_detects_changed_observations_without_upstream_hash() -> None:
    from aeso_mcp.models.common import DatasetMetadata, ProviderName
    from aeso_mcp.models.operations import PageInfo
    from aeso_mcp.models.prices import PoolPriceInterval, PoolPriceResponse
    from aeso_mcp.services.provenance import analysis_source_from_response

    timestamp = datetime(2026, 8, 3, tzinfo=UTC)
    response = PoolPriceResponse(
        page=PageInfo(offset=0, limit=1, returned=1, total=1),
        intervals=[
            PoolPriceInterval(
                interval_start=timestamp,
                interval_end=timestamp.replace(hour=1),
                pool_price_cad_per_mwh=40,
            )
        ],
        metadata=DatasetMetadata(
            dataset="Pool Price", provider=ProviderName.AESO_APIM, retrieved_at=timestamp
        ),
    )
    changed = response.model_copy(
        update={
            "intervals": [response.intervals[0].model_copy(update={"pool_price_cad_per_mwh": 80})]
        }
    )
    first = analysis_source_from_response(
        response, role="focus", dataset="Pool Price", requested_start=None, requested_end=None
    )
    second = analysis_source_from_response(
        changed, role="focus", dataset="Pool Price", requested_start=None, requested_end=None
    )
    assert first.source_hash is not None
    assert first.source_hash != second.source_hash
