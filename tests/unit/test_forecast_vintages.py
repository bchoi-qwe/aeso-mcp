"""Point-in-time forecast-vintage storage and selection contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from aeso_mcp.config import Settings
from aeso_mcp.models.common import DataCompleteness, FinalityStatus
from aeso_mcp.models.forecasts import ForecastInterval, ForecastVintage, OfficialForecastRequest
from aeso_mcp.models.history import HistoricalDataset
from aeso_mcp.providers.forecasts import (
    AesoForecastProvider,
    _parse_current_forecast_csv,
    _parse_pool_price_forecast_csv,
)
from aeso_mcp.services.cache import AsyncTTLCache
from aeso_mcp.services.forecasts import ForecastService, _annotate_intervals, _intervals_hash
from aeso_mcp.storage.history import HistoricalStore


def _vintage(
    target: datetime,
    *,
    issue: datetime,
    value: float,
    source_version: str = "v1",
    source_hash: str | None = None,
) -> ForecastVintage:
    return ForecastVintage(
        interval_start=target,
        interval_end=target + timedelta(hours=1),
        series="wind",
        horizon="historical",
        forecast_issue_time=issue,
        publication_time=issue + timedelta(minutes=5),
        retrieved_at=issue + timedelta(minutes=6),
        source_version=source_version,
        source_hash=source_hash or f"hash-{value}",
        forecast_value=value,
        actual_value=50.0,
        unit="MW",
        source_product="Wind historical fixture",
        vintage_id=f"fixture-{source_version}-{value}",
    )


def _store(tmp_path: Path) -> HistoricalStore:
    store = HistoricalStore(tmp_path / "history")
    if not store.dependencies_available():
        pytest.skip("analytics extra is not installed")
    return store


def test_forecast_request_requires_timezone_aware_as_of() -> None:
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        OfficialForecastRequest(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 1, 2, tzinfo=UTC),
            as_of=datetime(2025, 1, 1),
        )


def test_store_preserves_vintages_and_selects_latest_eligible_target(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = datetime(2025, 1, 1, 18, tzinfo=UTC)
    issue_early = target - timedelta(hours=10)
    issue_late = target - timedelta(hours=4)

    assert store.upsert_forecast_vintages(
        [
            _vintage(target, issue=issue_early, value=100.0),
            _vintage(target, issue=issue_late, value=80.0),
            _vintage(target, issue=issue_late, value=81.0, source_version="v2"),
        ]
    ) == (3, 1)

    all_rows, all_total = store.query_forecast_vintages(
        "wind",
        start=target,
        end=target + timedelta(hours=1),
        latest_per_target=False,
        paginate=False,
    )
    assert all_total == 3
    assert {row.forecast_value for row in all_rows} == {100.0, 80.0, 81.0}

    refreshed = _vintage(
        target,
        issue=issue_late,
        value=81.0,
        source_version="v2",
    ).model_copy(
        update={
            "actual_value": 55.0,
            "retrieved_at": issue_late + timedelta(days=1),
        }
    )
    assert store.upsert_forecast_vintages([refreshed]) == (1, 1)
    refreshed_rows, refreshed_total = store.query_forecast_vintages(
        "wind",
        start=target,
        end=target + timedelta(hours=1),
        latest_per_target=False,
        paginate=False,
    )
    assert refreshed_total == 3
    assert next(row for row in refreshed_rows if row.forecast_value == 81.0).actual_value == 55.0

    as_of_rows, as_of_total = store.query_forecast(
        "wind",
        start=target,
        end=target + timedelta(hours=1),
        as_of=issue_late + timedelta(minutes=5),
        paginate=False,
    )
    assert as_of_total == 1
    assert [row.forecast_value for row in as_of_rows] == [81.0]

    current_rows, current_total = store.query_forecast(
        "wind",
        start=target,
        end=target + timedelta(hours=1),
        paginate=False,
    )
    assert current_total == 1
    assert current_rows[0].forecast_value == 81.0


def test_store_vintage_identity_ignores_raw_hash_when_forecast_is_enriched(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    target = datetime(2025, 1, 1, 18, tzinfo=UTC)
    original = _vintage(target, issue=target - timedelta(hours=4), value=80.0, source_hash="raw-v1")
    enriched = original.model_copy(
        update={
            "actual_value": 55.0,
            "finality": FinalityStatus.FINAL,
            "retrieved_at": target + timedelta(days=1),
            "source_hash": "raw-v2",
        }
    )

    assert store.upsert_forecast_vintages([original]) == (1, 1)
    assert store.upsert_forecast_vintages([enriched]) == (1, 1)

    rows, total = store.query_forecast_vintages(
        "wind",
        start=target,
        end=target + timedelta(hours=1),
        latest_per_target=False,
        paginate=False,
    )

    assert total == 1
    assert rows[0].actual_value == 55.0
    assert rows[0].source_hash == "raw-v2"


def test_store_as_of_selection_respects_requested_horizon(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = datetime(2025, 1, 1, 18, tzinfo=UTC)
    historical = _vintage(
        target,
        issue=target - timedelta(days=1),
        value=100.0,
        source_version="historical",
    )
    current = _vintage(
        target,
        issue=target - timedelta(hours=1),
        value=80.0,
        source_version="current",
    ).model_copy(update={"horizon": "current_7_day"})
    assert store.upsert_forecast_vintages([historical, current]) == (2, 1)

    rows, total = store.query_forecast(
        "wind",
        start=target,
        end=target + timedelta(hours=1),
        as_of=target,
        horizon="historical",
        paginate=False,
    )

    assert total == 1
    assert rows[0].horizon == "historical"
    assert rows[0].forecast_value == 100.0


def test_store_keeps_dst_fall_back_target_instants_distinct(tmp_path: Path) -> None:
    store = _store(tmp_path)
    market_tz = ZoneInfo("America/Edmonton")
    day_start = datetime(2024, 11, 3, tzinfo=market_tz)
    day_end = datetime(2024, 11, 4, tzinfo=market_tz)
    start_utc = day_start.astimezone(UTC)
    rows = [
        _vintage(
            start_utc + timedelta(hours=index),
            issue=start_utc - timedelta(days=1),
            value=float(index),
            source_version="dst",
            source_hash=f"dst-{index}",
        )
        for index in range(25)
    ]
    assert store.upsert_forecast_vintages(rows) == (25, 1)

    selected, total = store.query_forecast(
        "wind",
        start=day_start,
        end=day_end,
        paginate=False,
    )
    assert total == 25
    assert len(selected) == 25
    assert selected[1].interval_start.utcoffset() != selected[2].interval_start.utcoffset()


def test_store_migrates_v2_market_forecast_without_losing_actual(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    root = tmp_path / "history"
    root.mkdir()
    connection = duckdb.connect(str(root / "history.duckdb"))
    connection.execute(
        """
        CREATE TABLE market_series (
            interval_start TIMESTAMPTZ NOT NULL,
            interval_end TIMESTAMPTZ,
            series VARCHAR NOT NULL,
            actual_value DOUBLE,
            forecast_value DOUBLE,
            unit VARCHAR NOT NULL,
            source_product VARCHAR NOT NULL,
            PRIMARY KEY (interval_start, series)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO market_series VALUES (
            TIMESTAMPTZ '2025-01-01 00:00:00+00',
            TIMESTAMPTZ '2025-01-01 01:00:00+00',
            'pool_price', 42.0, 40.0, 'CAD/MWh', 'legacy report'
        )
        """
    )
    connection.close()

    store = HistoricalStore(root)
    store.initialize()
    actual_rows, actual_total = store.query_pool_price(
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 1, 1, 1, tzinfo=UTC),
        paginate=False,
    )
    migrated_rows, migrated_total = store.query_forecast(
        "pool_price",
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 1, 1, 1, tzinfo=UTC),
        paginate=False,
    )
    assert actual_total == 1
    assert actual_rows[0].actual_value == 42.0
    assert migrated_total == 1
    assert migrated_rows[0].forecast_value == 40.0
    assert migrated_rows[0].actual_value == 42.0
    assert (
        store.query_forecast(
            "pool_price",
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 1, 1, 1, tzinfo=UTC),
            as_of=datetime(2025, 1, 2, tzinfo=UTC),
            paginate=False,
        )[1]
        == 0
    )


def test_store_migration_deduplicates_legacy_load_alias_on_refresh(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    root = tmp_path / "history"
    root.mkdir()
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    connection = duckdb.connect(str(root / "history.duckdb"))
    connection.execute(
        """
        CREATE TABLE market_series (
            interval_start TIMESTAMPTZ NOT NULL,
            interval_end TIMESTAMPTZ,
            series VARCHAR NOT NULL,
            actual_value DOUBLE,
            forecast_value DOUBLE,
            unit VARCHAR NOT NULL,
            source_product VARCHAR NOT NULL,
            PRIMARY KEY (interval_start, series)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO market_series VALUES (
            TIMESTAMPTZ '2025-01-01 00:00:00+00',
            TIMESTAMPTZ '2025-01-01 01:00:00+00',
            'load', 100.0, 90.0, 'MW', 'legacy report'
        )
        """
    )
    connection.close()

    store = HistoricalStore(root)
    store.initialize()
    store.upsert_market_series(
        HistoricalDataset.LOAD,
        [
            {
                "interval_start": start,
                "interval_end": end,
                "series": "ail",
                "actual_value": 101.0,
                "forecast_value": 91.0,
                "unit": "MW",
                "source_product": "refreshed report",
            }
        ],
    )

    rows, total = store.query_ail(start=start, end=end, paginate=False)
    status = next(item for item in store.statuses() if item.dataset == HistoricalDataset.LOAD)

    assert total == 1
    assert len(rows) == 1
    assert rows[0].actual_value == 101.0
    assert rows[0].source_product == "refreshed report"
    assert status.observation_count == 1
    assert status.earliest_interval is not None
    assert status.earliest_interval.tzinfo is not None
    assert getattr(status.earliest_interval.tzinfo, "key", None) == "America/Edmonton"


def test_fallback_vintage_hash_ignores_later_actual_enrichment() -> None:
    target = datetime(2025, 1, 1, 18, tzinfo=UTC)
    base = ForecastInterval(
        interval_start=target,
        interval_end=target + timedelta(hours=1),
        series="wind",
        horizon="historical",
        forecast_value=80.0,
        actual_value=None,
        forecast_issue_time=target - timedelta(hours=4),
        unit="MW",
        source_product="fixture",
    )
    enriched = base.model_copy(update={"actual_value": 55.0})

    assert _intervals_hash([base]) == _intervals_hash([enriched])


def test_fallback_vintage_hash_is_stable_when_fetch_range_is_split() -> None:
    start = datetime(2025, 1, 1, 18, tzinfo=UTC)
    first = ForecastInterval(
        interval_start=start,
        interval_end=start + timedelta(hours=1),
        series="ail",
        horizon="historical",
        forecast_value=80.0,
        actual_value=50.0,
        unit="MW",
        source_product="fixture",
    )
    second = first.model_copy(
        update={
            "interval_start": start + timedelta(hours=1),
            "interval_end": start + timedelta(hours=2),
            "forecast_value": 81.0,
            "actual_value": 51.0,
        }
    )
    retrieved_at = start + timedelta(days=1)

    combined = _annotate_intervals(
        [first, second], provenance={}, publication_time=None, retrieved_at=retrieved_at
    )
    split = _annotate_intervals(
        [first], provenance={}, publication_time=None, retrieved_at=retrieved_at
    )

    assert combined[0].source_hash == split[0].source_hash


def test_forecast_models_accept_storage_target_aliases() -> None:
    target = datetime(2025, 1, 1, 18, tzinfo=UTC)
    interval = ForecastInterval(
        target_interval_start=target,
        target_interval_end=target + timedelta(hours=1),
        issue_time=target - timedelta(hours=4),
        series="wind",
        horizon="historical",
        forecast_value=80.0,
        unit="MW",
        source_product="fixture",
    )
    vintage = ForecastVintage(
        target_interval_start=target,
        target_interval_end=target + timedelta(hours=1),
        issue_time=target - timedelta(hours=4),
        source_retrieved_at=target,
        series="wind",
        horizon="historical",
        forecast_value=80.0,
        unit="MW",
        source_product="fixture",
    )

    assert interval.interval_start == target
    assert interval.forecast_issue_time == target - timedelta(hours=4)
    assert vintage.target_interval_end == target + timedelta(hours=1)
    assert vintage.retrieved_at == target


def test_current_forecast_parser_keeps_fall_back_targets_distinct() -> None:
    raw = (
        b"Forecast Transaction Date,Min,Most Likely,Max,Actual,MCR\n"
        b"2024-11-03 00:50,1,2,3,2,4\n"
        b"2024-11-03 01:00,1,2,3,2,4\n"
        b"2024-11-03 01:00,1,3,4,3,4\n"
    )

    rows = _parse_current_forecast_csv(raw, series="wind", horizon="current_12_hour")

    assert len(rows) == 3
    assert rows[1].interval_start.utcoffset() != rows[2].interval_start.utcoffset()
    assert rows[2].interval_start.astimezone(UTC) > rows[1].interval_start.astimezone(UTC)


def test_pool_price_report_date_does_not_claim_publication_midnight() -> None:
    raw = (
        b"Forecast and Actual Pool Price\n"
        b"August 26, 2026\n"
        b"Date,Forecast Pool Price,Actual Posted Pool Price,Forecast AIL,Actual AIL\n"
        b"08/26/2026 1,42.50,40.00,10500,10475\n"
    )

    rows, report_date = _parse_pool_price_forecast_csv(raw)

    assert report_date is not None
    assert all(row.publication_time is None for row in rows)


@pytest.mark.asyncio
async def test_pool_price_date_only_report_is_not_eligible_before_known_publication_time() -> None:
    provider = AsyncMock(spec=AesoForecastProvider)
    start = datetime(2026, 8, 26, 0, tzinfo=UTC)
    provider.get_pool_price_forecast.return_value = (
        [
            ForecastInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                series="pool_price",
                horizon="historical",
                forecast_value=42.5,
                actual_value=40.0,
                unit="CAD/MWh",
                source_product="Forecast and Actual Pool Price",
            )
        ],
        None,
        {"provider": "aeso_public_report", "source_report_date": "2026-08-26"},
    )
    service = ForecastService(
        provider,
        Settings(aeso_api_key=SecretStr("test-key")),
        AsyncTTLCache(),
    )

    response = await service.get_forecast(
        OfficialForecastRequest(
            start=start,
            end=start + timedelta(hours=1),
            series="pool_price",
            as_of=datetime(2026, 8, 26, 23, tzinfo=UTC),
        )
    )

    assert response.intervals == []
    assert response.metadata.completeness is DataCompleteness.UNKNOWN
    assert any("known publication" in warning for warning in response.warnings)


@pytest.mark.asyncio
async def test_forecast_service_as_of_filters_provider_rows_without_lookahead() -> None:
    provider = AsyncMock(spec=AesoForecastProvider)
    target = datetime(2025, 1, 1, 18, tzinfo=UTC)
    early_issue = target - timedelta(hours=10)
    late_issue = target - timedelta(hours=4)
    rows = [
        ForecastInterval(
            interval_start=target,
            interval_end=target + timedelta(hours=1),
            series="wind",
            horizon="historical",
            forecast_issue_time=early_issue,
            publication_time=early_issue + timedelta(minutes=5),
            forecast_value=100.0,
            actual_value=50.0,
            source_version="early",
            source_hash="early-hash",
            unit="MW",
            source_product="fixture",
        ),
        ForecastInterval(
            interval_start=target,
            interval_end=target + timedelta(hours=1),
            series="wind",
            horizon="historical",
            forecast_issue_time=late_issue,
            publication_time=late_issue + timedelta(minutes=5),
            forecast_value=80.0,
            actual_value=50.0,
            source_version="late",
            source_hash="late-hash",
            unit="MW",
            source_product="fixture",
        ),
    ]
    provider.get_historical_forecast.return_value = (rows, None, {"provider": "aeso_public_report"})
    service = ForecastService(
        provider,
        Settings(aeso_api_key=SecretStr("test-key")),
        AsyncTTLCache(),
    )

    response = await service.get_forecast(
        OfficialForecastRequest(
            start=target,
            end=target + timedelta(hours=1),
            series="wind",
            horizon="historical",
            as_of=early_issue + timedelta(minutes=5),
        )
    )
    assert len(response.intervals) == 1
    assert response.intervals[0].forecast_value == 100.0
    assert response.intervals[0].forecast_issue_time == early_issue
    assert response.metadata.extra["vintage_selection"] == "latest_eligible_per_target"
    assert response.metadata.completeness is DataCompleteness.UNKNOWN
    assert "unobserved" in response.warnings[0]


@pytest.mark.asyncio
async def test_forecast_service_marks_missing_target_intervals_partial() -> None:
    provider = AsyncMock(spec=AesoForecastProvider)
    start = datetime(2025, 1, 1, 18, tzinfo=UTC)
    provider.get_historical_forecast.return_value = (
        [
            ForecastInterval(
                interval_start=start,
                interval_end=start + timedelta(hours=1),
                series="wind",
                horizon="historical",
                forecast_value=80.0,
                actual_value=50.0,
                unit="MW",
                source_product="fixture",
            )
        ],
        None,
        {"provider": "aeso_public_report"},
    )
    service = ForecastService(
        provider,
        Settings(aeso_api_key=SecretStr("test-key")),
        AsyncTTLCache(),
    )

    response = await service.get_forecast(
        OfficialForecastRequest(
            start=start,
            end=start + timedelta(hours=2),
            series="wind",
            horizon="historical",
        )
    )

    assert response.metadata.completeness is DataCompleteness.PARTIAL
    assert response.metadata.expected_observations == 2
    assert response.metadata.missing_observations == 1
    assert response.warnings == [
        "1 forecast target interval(s) were unobserved in the requested range."
    ]
