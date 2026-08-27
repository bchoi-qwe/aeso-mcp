# Research analytics

The research tools consume complete internal source results rather than a user-visible page.
They are deterministic: the same source observations and request produce the same calculation.

| Tool | Calculation |
| --- | --- |
| `get_price_statistics` | distribution, volatility, percentiles, negative/high-price hours |
| `get_price_duration_curve` | descending price curve with exceedance percentage |
| `analyze_market_event` | focus versus baseline price, demand, supply, merit-order, intertie, commitment, and reserve evidence |
| `calculate_capture_prices` | generation-weighted Pool Price by asset or fuel |
| `analyze_net_load` | AIL minus selected CSD renewable generation |
| `analyze_supply_stack` | one-hour offer stack and marginal dispatched offer |
| `analyze_intertie_utilization` | gross-offer to available-capability proxy |
| `analyze_generation_mix` | CSD energy and share by fuel |
| `analyze_asset_dispatch` | energy, output, capacity factor, and hourly ramps |
| `analyze_outage_impact` | outage-price grouping and Pearson association |
| `analyze_forecast_error` | forecast-minus-actual bias, MAE, RMSE, MAPE, hourly profile |
| `calculate_asset_energy_revenue` | matched hourly metered MWh × Pool Price gross energy revenue |
| `compare_csd_to_metered` | operational CSD MW converted by interval duration versus metered MWh |
| `analyze_ramps` | cadence-aware AIL, net-load, wind, solar, or selected-asset ramps |
| `analyze_supply_surplus_events` | explicit surplus-event bounds aligned to price, load, and renewables |

## Interpretation boundaries

`analyze_market_event` returns structured evidence for price, AIL and forecast error, ramps,
generation/capability/outages, renewable share and net load, merit-order position and asset-level
offer-volume HHI, import/export capability and gross offers, intertie outages, reserve prices,
volumes, activations and offer-control blocks, and commitment counts. It ranks focus-versus-
baseline changes, but optional reports that are outside their publication window remain explicit
warnings rather than zeroes.

Official forecast-error, adequacy/cushion, supply-surplus, FFR schedule, DDS, TMR reference-price,
and system-event context is added when its source covers the event window. Authentication failures
on required authenticated inputs remain hard failures; optional public-report failures produce
warnings and degraded completeness.

`analyze_market_event` and `analyze_outage_impact` report descriptive associations. Neither
establishes causation. A useful research answer should state the time window, source completeness,
missing series, and which additional evidence would be needed for a causal conclusion.

The intertie metric uses gross offers divided by available transfer capability. It is an
offer-to-capability proxy, not metered interchange flow.

Asset energy revenue is only the matched `metered MWh × hourly Pool Price` gross energy component.
It is not total settlement revenue and excludes operating reserves, uplift, unit commitment,
transmission, and other settlement components. CSD comparison keeps operational average MW and
settlement-metered MWh distinct. Supply-surplus and event results are observed associations, not
causal attribution.
