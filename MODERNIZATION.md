# Modernization roadmap

Status: baseline implementation merged across all five phases; several acceptance gates remain open. Baseline: main at 93e6b614a2a009c09eabc5d59ca10f95fdee716f. Research date: 2026-11-27.

## Principles

- Treat the MCP as a typed, deterministic interface to authoritative Alberta market evidence, not as an LLM or a generic API wrapper.
- Preserve source-specific publication times, units, UTC interval identity, retrieval history, missingness and finality. Never infer finality from age, fill missing observations with zero or use future information in historical research.
- Keep model training/forecasting and bulk compute outside MCP. Do not introduce arbitrary SQL, shell, URL or code-execution tools.
- Compatibility is not a requirement for redesigned contracts; nevertheless retain supported transports when there is no measurable cost to removing them.
- Use stable released dependencies, reproducible locked builds, small reviewable pull requests and measured acceptance criteria. No automatic publication or production deployment.

## Evidence and current baseline

- [MCP 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/) is the current published protocol. The repository already implements its modern path and has conformance gates. [The new roadmap](https://blog.modelcontextprotocol.io/posts/mcp-roadmap/) identifies discovery/context overhead as a priority; [release cadence](https://blog.modelcontextprotocol.io/posts/mcp-release-cycle/) is six-monthly. Do not target draft revisions as production standards.
- [FastMCP 4.2.6](https://pypi.org/project/fastmcp/4.2.6/) is the latest verified stable release on the research date; upgraded from 4.2.0 in PR #11 with a CI-resolved and audited lockfile.
- [uv lock discipline](https://docs.astral.sh/uv/concepts/projects/sync/) and [Dependabot uv support](https://docs.astral.sh/uv/guides/integration/dependabot/) support reviewable dependency and lockfile upgrades.
- [OpenTelemetry conventions](https://opentelemetry.io/docs/specs/semconv/http/) inform transport and upstream metrics and traces; avoid high-cardinality raw arguments or credentials.
- [AESO transition](https://www.aeso.ca/transition/) and [REM ISO Rules](https://www.aeso.ca/rules-standards-and-tariff/rem-iso-rules/) require market-regime-aware source contracts. The current IT roadmap targets Q3 2027 system trials and Q2 2028 market cutover; neither is a verified API launch date.
- [PyPI project](https://pypi.org/project/aeso-mcp/) listed 0.3.0 in retrieved public metadata; source/registry declare 0.4.0. Verify live package state again before release.

Current system has 64 tools, typed models, separate providers/services, bounded HTTP, forecast-vintage history, deterministic provenance manifests and full/compact tool discovery. Existing evals are not real-model benchmarks. Existing storage is single-process/local, not an immutable all-dataset warehouse.

## Phase 1 — reliability and release foundation (first PR)

- Fix cache invalidation during in-flight requests: old callers may complete, but invalidated data must not be reinserted or joined by new callers. Regression test concurrent clear/refresh.
- Correct optional APIM key in Registry and check the contract at release.
- Correct stale dependency-version documentation; enable reviewable uv and Actions dependency updates.
- Run public live tests without an APIM credential and fail authenticated CI live tests explicitly if the protected secret is missing. Check price finiteness rather than an unsupported timeless nonnegative-price invariant.
- Review PR diff and require existing locked CI, independent conformance, strict docs, installed wheel and Docker checks before merge. Do not label live integration verified unless a live run actually executes.

## Phase 2 — runtime, security and supply chain

1. Update FastMCP to current stable patch; regenerate uv.lock, rerun all gates. Update exact version references and canary constraints, and review generated catalog differences.
2. Inventory dependencies and workflows; add lockfile vulnerability audit, immutable action SHA pinning, dependency review, SBOM and provenance for release artifacts. Pin and validate the selected tooling before adding required gates.
3. Threat model HTTP and upstream clients: test Host/Origin spoofing, request bodies, cancellation, limits, redirect and decompression/ZIP expansion, log redaction, token forwarding, and expensive parallel calls. Add deadline and byte/memory budgets with load tests. Keep static auth private-only; implement standards-compliant OAuth resource metadata/token validation if public multi-user HTTP is a goal.
4. Instrument tool, provider and store duration/error/freshness metrics using bounded labels; preserve protocol stdio stdout. Measure p50/p95 latency, peak RSS, response bytes, cache-hit and upstream request count against representative workloads.
5. Verify authenticated canary execution and public endpoint coverage; audit actual observed timestamps, source schema changes and publication delays. Distinguish unavailable/skipped/failed from healthy.

Acceptance: locked deterministic suite and independent modern conformance pass; no secret leakage; explicit tested resource budgets; documented measured latency and peak RSS; real canary evidence with timestamp and scope.

## Phase 3 — agent-facing contracts

1. Benchmark full catalog and compact discovery on the current canonical cases, extending with ambiguous, multi-tool, no-tool and incomplete-data prompts. Use real identified client/model versions, fixed fixtures, repeated trials and independent scoring. Measure correct tool and argument selection, grounded numeric answers, caveat/abstention accuracy, tokens, calls and latency.
2. Experiment with fewer consolidated typed tool families, compact summaries, bounded pagination, selective schema disclosure and deterministic domain synonyms/semantic ranking. Do not expose arbitrary execution to reduce tool calls.
3. Make discovery mode/default and removals evidence-based; assess success on held-out questions rather than only training examples. Preserve clear dataset semantics and annotations for read-only vs local writes.
4. Publish reproducible benchmark runs and traces with sensitive values removed.

Acceptance: report baseline and treatment on held-out cases, with no loss in numerical correctness or necessary caveats; select a default only after measured improvement. No invented model scores.

## Phase 4 — data platform

1. Catalog each verified upstream report: identifier, market regime, source URL/product version, cadence, publication delay, revision/finality, availability bounds, units, license/access and schema.
2. Keep immutable source objects by SHA-256 and record observed retrieval/publication time separately. Use source-specific versioned schemas and normalized UTC keys; retain source timezone and units. Verify revisions and gap recovery with fixtures and real observations. Unknown chronology is ineligible for point-in-time reads.
3. Separate bounded ingestion CLI/worker from MCP runtime. Use a single writer, atomic checkpoints and restart-safe idempotency; document local storage locking and retention. Introduce a shared database only when deployment requires it.
4. Expand incremental ingestion beyond existing CSD, Pool Price and AIL plus forecast vintages only after source contracts and publication schedules are verified. Provide a Python/Parquet data access interface for bulk research and aesoTIME; MCP remains compact.
5. Add typed aligned multi-series/replay queries, with explicit join policy, coverage, provenance and no leakage. Test missingness, DST, late arrival, corrections, duplicate IDs and crash recovery.

Acceptance: repeatable offline retrieval of archived inputs; identical replay at a fixed information cutoff; no late observation leakage; bounded memory/latency; explicit data-quality and source-version metadata.

## Phase 5 — REM and release

- Track approved and effective ISO rules separately; add a market-regime dimension and source/version/effective-date registry before REM ingestion. Do not reinterpret legacy Pool Price or settlement concepts as REM products. Wait for authoritative API schema/publication documentation and observed samples before enabling new datasets.
- Add advanced analytics only when underlying datasets and methods are defensible (e.g., residual demand, conditional historical comparisons); label descriptive association rather than causation.
- Conduct a release audit of actual package, lock, Registry metadata, documentation, conformance, security, live test evidence and benchmark results. Version breaking contracts explicitly. Publish only as a separate authorized release operation.

## Work order

Phase 1 first, then 2 and 3 in parallel only on separate reviewed changes. Phase 4 requires source-contract evidence; Phase 5 requires official REM information. Large storage migrations, OAuth and new analytics are not justified as blind one-shot edits. Record actual CI and canary results for each change. 


## Implementation evidence (2026-11-27)

- #10: reliability and metadata fixes merged; 354 tests passed.
- #11: FastMCP 4.2.6, audited dependency lock, HTTP limits/deadlines, native usage telemetry,
  SBOM and future release provenance, runtime discovery benchmark merged; 370 tests passed.
  The audit of the revised lock reported no known vulnerabilities in 119 packages at the time.
- #16: evaluation integrity improvements merged; 357 tests passed. No real-model benchmark
  has been run; no change to the default discovery mode is justified yet.
- #22: content-addressed opt-in CSD archive, streaming normalization and ingestion CLI merged;
  363 tests passed.
- #23: explicit reviewed market-contract identity and REM safeguards merged; 359 tests passed.
- #24: offline single-source CSD replay with retrieval cutoff and integrity checks merged;
  367 tests passed.
- Combined main (f0454b8a92415391841114ab3942b168ed281f0a): 372 tests, 84.16% coverage,
  locked dependency audit, Docker, conformance and installed wheel checks passed.
- #25: public live canary exposed the October 2026 CSD fixed-MDT header and unpublished
  Supply Surplus statuses. The corrected candidate passed all four credential-free integration
  groups; authenticated APIM and the broader publication timing/coverage audit remain unverified.

Remaining acceptance work: actual agent/held-out benchmark with an identified model/client,
authenticated upstream canary, all-dataset source chronology and multi-series replay, memory
measurement under actual large-data workloads, and verified REM production source contracts.
Do not interpret the implementation of a phase's first increment as satisfying all of its
acceptance gates. Do not publish a release from this record alone.


## Follow-up acceptance evidence (2026-11-27)

- #25 merged after 376 deterministic tests and all four public integration groups passed.
- #26 added observed-state Pool Price/AIL replay; 378 tests passed. No legacy actual-vintage
  reconstruction or embedded-forecast inference.
- #27 added the reproducible 12-question CPU routing baseline and full hourly archive workload.
  The 0.5B model produced no valid bare JSON selections in either mode. A later diagnostic
  accepting only one JSON fence recovered 3/12 exact full and 6/12 exact compact routes,
  with no correct abstentions in either mode. Compact used 6,368 input tokens versus 37,465.
  This does not support changing the default without stronger client/model evidence.
- #28 and #30 separated public and protected authenticated runs. The trusted main run of #30
  passed all seven authenticated integration groups in 10.02 seconds. Together with public
  validation, all 11 existing live integration groups have been exercised successfully.
- #29 and #32 established complete hourly and five-minute CSD workload baselines.
- #34 streamed normalization directly into one atomic batched database transaction and passed
  382 tests, 84.25% coverage, conformance, Docker, wheel smoke, locked audit and all public live
  groups. The same source digests and row counts were reproduced. Hourly: 171,864 records,
  peak RSS 1,317,384 to 656,036 KiB. Five-minute: 1,427,328 records, peak RSS 8,504,976 to
  2,878,032 KiB. Five-minute end-to-end download/parse/write time was about 78.48 to 64.81 seconds.
  Runner hardware differs, so memory/time comparisons are observational, not controlled.
- #35 public metadata audit: 27 hourly files covering 2015-01-01 through 2026-10-31 and 98
  five-minute files covering 2015-01-01 through 2023-02-28. All advertised pages were checked;
  no unrecognized five-minute entries or filename date gaps. This is not row-level coverage.
- #33 initial 3B actual MCP synthetic fixture pilot scored 4/5 end-to-end.
- #37 merged a separately designed 54-case oracle benchmark (all 24 CI/evaluation gates
  passed): 0.5B 0/54, 1.5B 3/16, 3B 3/16. The 1.5B successes were numeric answers;
  3B successes were abstentions. Both larger models equal the 3/16 trivial
  always-abstain baseline on the shared subset.
- #38 native 3B Qwen function-calling pilot merged after all checks passed: 5/5
  parseable native tool syntax, 4/5 routes, 2/4 valid arguments, 1/5 end-to-end
  correct. Keep the default discovery mode and do not embed these models
  as autonomous agents on this evidence.
- #31 3B routing-only results: full catalog 10/12 exact routes vs compact 9/12;
  compact uses 6,368 vs 37,465 input tokens. The 1.5B complete aggregate remains
  pending. See `docs/model-validation-results-2026-11-28.md` and PR #31 for
  exact evidence scope.

Remaining scope limitations: no broad independent external-client numeric benchmark, no actual
REM production endpoint validation, no reconstructed publication history for previously unseen
sources, and no local desktop workload/deployment verification. Avoid a formal release until
release gates and intended client/deployment behavior are reviewed.


## 2026-11-28 REM endpoint verification

Official InfoHub published an MUI API specification and JSON preview in October 2026,
but AESO's public transition timeline places REM soft launch in 2027 and cutover in Q2 2028.
The existing working APIM is not a verified new REM production API. New REM endpoint base URLs
remain unset, and the offline OpenAPI reviewer does not probe declared servers. See
`docs/rem-api-readiness.md` for source links, preproduction inventory and mandatory
read-only production acceptance gates.
