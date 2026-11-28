# REM API readiness — evidence as of 2026-11-28

**Conclusion:** the official existing AESO APIM public-report APIs are already operating
and the project passed seven protected authenticated live test groups. This does **not**
validate a Restructured Energy Market (REM) production endpoint.

## Verified primary sources

- [AESO REM IT Systems Implementation](https://aesoengage.aeso.ca/rem-it-systems-implementation)
  reports a preview of the new MMS/MUI User Guide, MUI API Specification and
  `AESO_MUI_API.json` published on 2026-10-09.
- [Direct public InfoHub MUI resource](https://infohub.aeso.ca/resources/detail/?id=0d33b95e-60c3-f111-aaad-000d3ae913fc)
  identifies the three attachments, release version 1.0, and Q1 2027 soft-launch context.
  The JSON attachment itself has **not** been retrieved and audited here.
- [AESO IT Systems](https://www.aeso.ca/transition/it-systems/) lists soft launch January
  through Q3 2027, Q3 2027 system trials, and Q2 2028 market cutover.
- [AESO legacy/public APIM](https://www.aeso.ca/market/market-and-system-reporting/aeso-application-programming-interface-api/)
  lists the currently operating public-report APIs; these must not be confused with MMS
  or other new REM interfaces.
- AESO's 2026 working groups discuss MMDS dispatch messaging, settlement and operating
  reserves APIs, but publicly available descriptions are **not** authoritative deployment
  URLs or proof of accessible production services.

The reviewed machine-readable inventory is in `docs/rem-endpoint-inventory.json`.
The production and sandbox base URLs for new REM systems are intentionally null.
No client, DNS probe, authentication flow or data adapter is enabled from this inventory.

## Offline specification review

If an authorized user exports the actual `AESO_MUI_API.json` reference, run:

```bash
uv run python -m scripts.review_rem_openapi --spec path/to/AESO_MUI_API.json
```

This validates OpenAPI 3 or Swagger 2 shape and reports only its content hash, operation
counts, declared API version and HTTPS hostnames. It does **not** execute any operation,
upload the specification or treat declared server URLs as live endpoints. Do not commit
participant credentials, sandbox URLs, or non-public attachments.

## Required production integration gates

1. Obtain and hash the official released API specification and identify its effective date,
   intended environment, authentication model, lifecycle, permissions, schemas, rate limits
   and publication/finality guarantees.
2. Obtain written AESO confirmation of the actual production base URL and access eligibility.
   Sandbox invitations and the October preview are not production certification.
3. Using authorized credentials and a protected runner, test **read-only** production
   operations with explicit timeouts and redacted logs, validate results against independent
   AESO published values and confirm rate/error/clock behavior. Never submit bids, offers,
   dispatches, settlement instructions or production writes during exploratory tests.
4. Document the cutover, market regime identity and precedence over existing ETS/APIM data.
   Introduce versioned contracts only for verified REM reports; preserve old historical APIs.
5. Review and explicitly approve changes in a separate PR before updating production routing.

Until those gates are complete, the correct result is **REM production endpoints unverified**,
not a guessed URL or a legacy APIM endpoint mislabeled as REM.
