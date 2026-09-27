# 0.4.0 release-candidate audit — 2026-09-26

Status: **buildable candidate; release sign-off incomplete**. No commit, tag, push, publication, or
external registry mutation was performed.

Audited HEAD: `535a14a` (`starting`), `main...origin/main`. The worktree was already dirty when
this audit began, with substantial 0.4.0 source, test, and documentation changes plus this report
present. Those changes were preserved; nothing was reset or committed. The first long test output
in this continuation was truncated before a result, so it is not a reliable pre-edit baseline. The
final checks below are authoritative for the current combined worktree.

## Findings and changes verified

1. **FastMCP 4 is stable.** The application now pins `fastmcp==4.0.10`; the lockfile resolves
   matching `fastmcp-slim==4.0.10`, and the old prerelease resolver exception and exact beta
   allowlist are removed. CI rejects every locked PEP 440 prerelease; the non-blocking canary is
   constrained to stable FastMCP 4.0.x patches. Both MCP 2026-07-28 auto-negotiation and legacy
   2025-11-25 session negotiation list the full 63-tool surface.
2. **Progressive discovery remains an experiment.** A reproducible FastMCP BM25 prototype measures
   all 70 canonical cases. Top-five routing finds all 66 expected routes; first choice is correct or
   pinned in 57/66. Its measured catalog-and-query text is 85.4% smaller than repeatedly listing
   all tools, but it adds discovery calls and suggests tools for all four no-tool cases. The full
   catalog remains the default; there is no model-answer benchmark or claim of improved model
   performance.
3. **Forecast point-in-time selection is retrieval-aware.** Both in-memory and historical-store
   selectors require retrieval no later than `as_of`, and exclude records with unknown issue and
   publication chronology. Schema v3 preserves retrieval snapshots separately from the latest
   forecast view; migrations retain only available state and do not recreate overwritten history.
4. **Research provenance and completeness are conservative.** Typed response observations supply a
   deterministic SHA-256 fallback when no upstream hash exists; ordering includes tied source
   fields, datetimes are normalized, and date-valued observations are supported. Incomplete,
   truncated, or unverifiable research results are not mislabeled complete/final.
5. **Large official assets stay bounded.** The credential-free AESO report client streams response
   chunks and stops at a 25 MiB report or 384 MiB fixed-asset limit before assembling the complete
   response. Verified annual frequency members fit the finite archive-member cap, and the large
   frequency/planning-area parsers run off the event loop. No arbitrary URL surface was added.
6. **The live DDS test matches the product contract.** A rolling half-open window may correctly be
   empty when the report's latest publication precedes the requested start. The live canary now
   validates schema parsing, filtering, pagination, and empty-completeness metadata without
   requiring a publication on every run; deterministic tests cover the boundary behavior.
7. **Packaging and discovery checks are stronger.** CI includes packaging tests, checks the
   generated catalog, builds the wheel through the sdist, and tests the installed console entry
   point over stdio. Docker smoke now checks bearer enforcement plus modern and legacy HTTP
   negotiation. Release metadata, limitations, and discovery measurements are synchronized.

No new AESO dataset was added in this pass. The existing source matrix and `LIMITATIONS.md` retain
known gaps and distinguish unsupported coverage from zero observations or inferred data.

## Validation

| Command/check | Result |
| --- | --- |
| `uv sync --locked --group dev --extra analytics --extra docs` | PASS; 127 packages checked against the 132-package lock |
| `uv run ruff check src tests scripts` | PASS |
| `uv run ruff format --check src tests scripts` | PASS; 131 files |
| `uv run pyright src` | PASS; zero errors, warnings, or informations |
| `uv run pytest --cov=aeso_mcp -q` | PASS; 287 passed, 11 live tests skipped; 80.87% branch-aware coverage (75% floor) |
| `uv run python tests/packaging/check_lock_prereleases.py` / `uv lock --check` | PASS; no locked prereleases |
| `uv run python scripts/generate_catalog.py --check` | PASS |
| `uv run mkdocs build --strict` | PASS |
| `uv run python tests/packaging/check_release_metadata.py` | PASS; version 0.4.0 |
| `uv build --clear` | PASS; wheel built through the sdist |
| Fresh isolated wheel smoke | PASS; exact FastMCP 4.0.10, 63 tools, 30 resources, 3 prompts, packaged resource and tool validation |
| Installed console-script stdio smoke | PASS for modern 2026-07-28 and legacy 2025-11-25; both list all 63 tools |
| Authenticated local HTTP smoke | PASS; anonymous request returned 401; modern and legacy clients listed all 63 tools |
| Official MCP conformance active suite 0.1.16 | PASS against the application baseline: 12 passed, 20 expected unsupported-capability failures, zero unexpected failures |
| `bash -n tests/packaging/docker_runtime_smoke.sh` | PASS |
| `docker info` / Docker image runtime smoke | BLOCKED_EXTERNAL; Docker daemon unavailable, so image build/runtime was not executed |
| `tests/integration` with `.env` loaded without displaying its contents | PASS; 11 passed in 15.91s |
| `git diff --check` | PASS |

An expanded, non-CI `pyright src tests` invocation reports diagnostics in test files. The documented
and CI-enforced scope is `pyright src`, which passes; this optional broader result is not presented
as clean.

A prior credentialed run had 10 passes and one DDS nonempty-window assertion failure: AESO returned
a valid `09/10/2026 10:21,0` record just before the rolling half-open start. The assertion was
corrected to accept a schema-valid empty window while checking filtering and metadata. The full
real-key integration suite was then rerun with the key loaded from `.env` and passed all 11 tests.
The key was not printed or added to the report. Live data remains revisable and time-dependent.

## Release gates

1. Run the Docker image build and updated runtime smoke with a working Docker daemon.
2. Human review of `LIMITATIONS.md`, release metadata, security boundaries, and the final diff.
3. Keep publication and registry actions manual; none were performed by this audit.

## Built artifacts

- `dist/aeso_mcp-0.4.0-py3-none-any.whl` — SHA-256
  `448766c46145cd0a5c314c67d7290d274f587e4d41949776f6abaf23eecf89c3`
- `dist/aeso_mcp-0.4.0.tar.gz` — SHA-256
  `33dce37decd66140a4e8c865c6f675b5bbf647ce82f138aa3d7bff884ba764f2`
