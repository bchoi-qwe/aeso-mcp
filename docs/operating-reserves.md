# Operating reserves

AESO publishes active and standby reserve products for regulating reserve, spinning reserve, and
supplemental reserve. The MCP tools keep economically different price fields separate.

## Prices

`get_operating_reserve_prices` returns daily product/time-block observations:

- active price (`CAD/MW`);
- standby premium price (`CAD/MW`);
- standby activation strike price (`CAD/MWh`);
- standby clearing blended price (`CAD/MW`); and
- cleared volume (`MW`).

These fields are not interchangeable. `summarize_operating_reserve_market` uses active price for
active products and clearing blended price for standby products.

## Forecast and activation

`get_operating_reserve_forecast` returns AESO's current seven-day hourly active/standby volume
forecast. Forecast metadata is preliminary and must not be treated as realized procurement.

`get_operating_reserve_activations` returns hourly standby activation volume and weighted average
activation price. The summary tool volume-weights activation prices within each reserve product.

## Related but distinct report

`get_operating_reserve_offer_control` is an authenticated, delayed offer-control report. Offer
blocks, procured reserve prices, forecasts, and realized standby activations describe different
parts of the reserve market and are not silently merged.
