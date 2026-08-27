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
- AIL actual/forecast observations.

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

## Additional historical reports

- `get_forecast` provides a generalized forecast contract for AIL, Pool Price, wind, solar, and
  current combined wind/solar. Each source keeps its own cadence, horizon, issue/target timestamps,
  units, finality, and missing-actual semantics.
- `get_uc_settlement_summary` returns hourly public UC settlement amount in CAD and charged
  volume in MW for inclusive report dates.
