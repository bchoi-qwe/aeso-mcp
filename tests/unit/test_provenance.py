# SPDX-License-Identifier: MIT
"""Research provenance manifest behavior."""

from __future__ import annotations

from datetime import UTC, datetime

from aeso_mcp.models.common import DataCompleteness, FinalityStatus
from aeso_mcp.models.provenance import AnalysisSource
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
