# Methodology and provenance

## Source priority

1. Official authenticated AESO APIM for supported datasets.
2. GridStatus when it implements the same official AESO product reliably.
3. Named AESO machine-readable public reports through a credential-free, host-allow-listed
   client.
4. The official AESO CSD Box archive through a separate fixed-share adapter.

The APIM key is never attached to ETS or Box requests. There is no arbitrary URL, shell, or SQL
surface.

## Observation semantics

- Requests with timestamps use half-open `[start, end)` intervals.
- Inclusive report-date tools state that convention in their request and metadata.
- `America/Edmonton` is the market timezone; UTC is used for chronology and interval matching.
- `status`, `observation_type`, `finality`, and `completeness` are separate fields.
- Missing source values remain missing; they are not automatically replaced with zero.

## Generation energy

For hourly CSD intervals, research tools interpret published average MW over one hour as MWh.
Five-minute records remain MW observations and are not silently summed as energy without applying
their interval duration.

## Capture price

For matched hourly intervals:

```text
capture price = sum(max(generation, 0) * Pool Price) / sum(max(generation, 0))
capture rate  = capture price / arithmetic mean Pool Price
```

Zero or negative generation does not create a negative weighting denominator.

## Forecast error

Error is `forecast - actual`. The generalized service reports mean error, MAE, RMSE,
denominator-aware MAPE (excluding zero actuals), requested absolute-error percentiles, paired and
missing observation counts, and the same core errors by local market hour and forecast lead time
when issue timestamps are available. AIL and renewable values are MW; Pool Price is CAD/MWh.

## Market-event evidence

Focus-window metrics are compared with a distinct baseline. Absolute change is focus minus
baseline; percent change divides that difference by a nonzero baseline. Offer-volume HHI groups
reported energy blocks by asset before summing squared volume shares. Intertie utilization remains
gross offer divided by positive available transfer capability, separated by import/export
direction where available. Reserve procurement price, cleared volume, activation volume, and
offer-control block counts remain separate concepts.

Official adequacy/cushion bands are preserved as published categories rather than converted to
invented MW midpoints. The derived supply-tightness label remains a separate transparent screening
calculation. Supply-surplus event endings and durations exist only when a later published status
provides an explicit boundary. FFR Net Schedule values are scheduled transfer (imports negative,
exports positive), not FFR dispatch or activation.

## Association, not causation

Window comparisons, high-outage grouping, and Pearson correlation are descriptive. They do not
control for load, offers, constraints, outages, imports, weather, or simultaneous market changes.
