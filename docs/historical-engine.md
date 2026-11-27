# Historical engine

## Official archive

`get_historical_generation` reads AESO's official Historical CSD Generation Data archive. It
supports individual assets, all published fuel types, and two resolutions:

| Resolution | Maximum direct query | Intended use |
| --- | ---: | --- |
| Hourly | 366 days | research, capture prices, generation mix, dispatch |
| Five minute | 7 days | dispatch shape and short event windows |

AESO describes this archive as operational Current Supply Demand data. It is not
settlement-metered generation. Values are average MW for each interval.

## Timestamp handling

The archive publishes `Date (MST)` and `Date (MPT)`. The adapter treats `Date (MST)` as a fixed
UTC-7 instant, converts it to UTC, and then presents `America/Edmonton` market time. This avoids
inventing or collapsing an instant during the repeated fall-back hour.

Every interval includes local and UTC boundaries, resolution, asset/fuel fields, capability
fields, source file ID/name/update/retrieval time, observation type, finality, completeness, and
schema version.

## Incremental DuckDB and Parquet store

`sync_historical_store` can persist:

- historical CSD generation;
- hourly Pool Price; and
- AIL actual/forecast observations; and
- immutable AIL, Pool Price, wind, solar, and combined wind/solar forecast vintages as those
  publications are retrieved through `get_forecast`.

The DuckDB index owns deduplication keys and source manifests. Duplicate primary observations are
counted in sync results and reduced deterministically to the last source observation. Parquet
snapshots are rebuilt only for affected `dataset/year/month` partitions (with generation
resolution as an additional partition). Re-running an unchanged source file is idempotent.

Pool Price and AIL syncs fetch only missing contiguous ranges. A stored preliminary interval
remains refreshable; once the same key is replaced by final data it is no longer treated as a
backfill target. CSD source objects are replaced atomically when their upstream identity changes,
and affected partitions are rebuilt from the DuckDB index.

Existing `get_pool_prices`, `get_load`, and analytics calls prefer local rows only when the
complete requested UTC cadence is present and every required observation is non-preliminary.
Incomplete, partial, preliminary, current, or future coverage falls back to the live provider and
is never silently declared authoritative. The live provider remains the source used by sync, so
an explicit refresh cannot read its own stale local output.

DuckDB, PyArrow, archive parsing/hashing, and Parquet partition writes run outside the async event
loop. Partition replacement uses a same-directory temporary file and atomic rename. Sync windows
are split internally to the upstream Pool Price, AIL, and CSD request limits and emit MCP progress
without coupling the domain service to FastMCP.

`get_historical_store_status` reports observation coverage, source-file counts, Parquet
partitions, schema version, configured path, detected cadence gaps, and dependency availability.
The source manifest retains object identity/hash, update and retrieval times, row count, coverage,
observation semantics, and schema version for upstream lineage.

## Forecast vintages and `as_of`

Schema version 3 adds `forecast_vintages` and immutable `forecast_observations` relations. A vintage retains target interval,
forecast issue and publication times, horizon, forecast and actual values, source version/hash,
retrieval time, lead time, finality, completeness, and source-file identity. Its key uses the
canonical forecast-only payload rather than retrieval time or the raw file hash, so a raw report
that later fills in actual/finality fields enriches the same publication while a revised forecast
remains a distinct vintage. Each retrieval snapshot retains its own actuals, finality, and source
hash. Refreshing the latest view does not rewrite earlier point-in-time observations. Existing v3
stores seed snapshots from their retained state only; overwritten historical states cannot be
reconstructed. Schema-v1 and v2 market rows remain readable after additive migration.

`get_forecast(..., as_of=...)` selects the latest eligible vintage per target interval. Every known
issue and publication timestamp, and the retrieval time, must be at or before `as_of`; rows with unknown publication
chronology are excluded. Existing schema-v2 forecast values migrate additively and remain
available without `as_of`, but are deliberately excluded from point-in-time queries because their
original information time cannot be reconstructed.

Point-in-time results report unknown completeness unless the source establishes full persisted
coverage. A missing target interval is unobserved, never silently filled from a later forecast.

## Additional historical reports

- `get_forecast` provides a generalized forecast contract for AIL, Pool Price, wind, solar, and
  current combined wind/solar. Each source keeps its own cadence, horizon, issue/target timestamps,
  units, finality, and missing-actual semantics.
- `get_uc_settlement_summary` returns hourly public UC settlement amount in CAD and charged
  volume in MW for inclusive report dates.


## Original CSD source objects (opt-in)

Set `AESO_MCP_RAW_CSD_ARCHIVE_ENABLED=true` to persist downloaded CSD ZIP bytes under
`<history-root>/raw/objects/<sha256-prefix>/<sha256>`. Retrieval manifests are immutable JSON
files in `raw/observations`; they distinguish the actual download time from the source's
reported update time (which is not necessarily the first publication time). Different retrievals
of identical bytes share a single object; a revised file has a new SHA-256 object. The Python
`RawSourceArchive.read(digest)` method verifies the bytes before returning them.

The archive captures new downloads only, does not backfill historical vintages and does not
expose a point-in-time query for all market data. It is opt-in because the original ZIP files can
be large and disk retention is operator-managed. Use a local filesystem supporting atomic hard
links; a failed manifest write can leave an unreferenced object. Never substitute the archive
retrieval timestamp for an independently verified publication date.

CSV rows are streamed from the ZIP during parsing instead of materializing a second, entire
uncompressed list of dictionaries. Selected normalized records and the compressed input still
occupy memory, so full-file ingestion remains subject to workload-specific benchmarking.


## Separate ingestion process

The same bounded synchronization service can run without an MCP server or an AI client:

```bash
uv run --extra analytics python -m aeso_mcp.ingest \
  --dataset pool_price --dataset load \
  --start 2026-01-01T00:00:00Z --end 2026-01-02T00:00:00Z
```

Add `--dataset historical_generation` for CSD ingestion, and `--refresh` to request
re-fetching existing coverage. The command emits a structured JSON summary and returns a
nonzero status for domain errors. A scheduler can call it with an explicit sliding window
and a persistent local volume. Use one writer per history root; this is not a multi-process
warehouse or an always-on scheduler. GitHub-hosted runners are ephemeral and are suitable
for fixture tests, not durable historical storage.
