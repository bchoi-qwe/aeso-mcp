# Agent-use evaluation

The repository includes 70 canonical user questions in `tests/evals/cases.json`. Cases cover
current conditions, source selection, historical generation, store operations, research
analytics, operating reserves, timestamp/finality semantics, and safe refusal boundaries.

## What CI verifies

- every case has a unique ID and a non-empty question;
- every expected tool exists on the live MCP surface;
- every case identifies expected argument fields and authoritative datasets;
- every case identifies prohibited interpretations, required caveats, and any numerical
  relationships that should hold;
- all implemented tool families have canonical coverage; and
- the generated MCP catalog is current.

## Progressive discovery prototype

The deterministic evaluation also prototypes FastMCP's stable `BM25SearchTransform` against the
same 70 canonical cases. It keeps `get_market_snapshot`, `analyze_market_event`, and `get_forecast`
always visible and returns at most five search matches. Run the reproducible measurement with:

```bash
uv run python scripts/evaluate_tool_discovery.py
```

On the measured pre-discovery catalog, the full listing contains 63 tools and serializes to 356,427 characters
(about 89,106 tokens at a rough four-characters-per-token estimate). The transformed listing
contains three pinned tools plus `search_tools` and `call_tool`: 27,723 characters (about 6,930
rough tokens), a 92.2% smaller initial tool catalog. The recursive JSON-value schema for analysis
provenance makes that initial catalog slightly larger but avoids advertising unconstrained output
values.

Across the 66 cases with an expected tool, the 5-result search included every expected tool in its
candidate set in all 66 cases. The first BM25 result (or a pinned tool) was the expected route in
57/66 cases; nine cases ranked a different tool first. Eight cases were handled directly by a
pinned tool, leaving 58 search calls and 58 additional tool-call round trips compared with direct
full-catalog routing. Search results include the full input/output schemas; their total text was
1,642,926 characters across the 58 supported-route discoveries. The four no-tool probes also
returned suggestions (113,441 characters combined). Counting an initial catalog per case plus all
62 queried result sets (58 supported routes and four no-tool cases), the measured content was
3,696,977 characters versus 24,949,890 for the full catalog, an estimated 85.2% reduction. These
are wire-character estimates divided by four, not model-tokenizer measurements; they exclude other
prompt content and client caching.

The four no-tool safety/semantics cases are important counter-evidence: BM25 returned five tool
suggestions for each when probed. Search ranking is not an abstention mechanism. The benchmark uses
MCP-shaped in-process calls, not an external model or a matrix of third-party clients. Therefore,
progressive discovery is **not enabled by default**: its context reduction is substantial, but
actual model routing improvement and behavior across Inspector/other clients have not been
established, it adds a discovery round trip for specialized requests, and it can suggest an
irrelevant tool for a refusal question. The server retains the full interoperable catalog while
this is re-evaluated against identified client/model versions.

## Optional model benchmark

`scripts/evaluate_agent_results.py` scores a JSON result file containing each case ID, selected
tools, tool arguments, datasets used, caveats, numerical relationships, and the answer. It reports
separate recall for routing, required argument fields, source selection, caveats, relationships,
and required concepts, plus prohibited-interpretation avoidance. This lets maintainers compare
Claude, Codex, ChatGPT, or another MCP client without putting provider credentials or model SDKs
in the server.

Each result has this shape:

```json
{
  "id": "research-001",
  "selected_tools": ["calculate_capture_prices"],
  "tool_arguments": {
    "calculate_capture_prices": {
      "start": "2026-07-01T00:00:00-06:00",
      "end": "2026-08-01T00:00:00-06:00",
      "fuel_types": ["WIND", "SOLAR"]
    }
  },
  "datasets_used": ["Historical CSD Generation Data", "Pool Price"],
  "caveats": ["Do not interpret the result as realized profit."],
  "numerical_relationships": [
    "capture price equals sum of generation times Pool Price divided by total matched generation; capture rate equals capture price divided by market average"
  ],
  "answer": "..."
}
```

Run the scorer from the repository root:

```bash
uv run python scripts/evaluate_agent_results.py results.json
```

No external model benchmark is claimed by the repository's deterministic CI. The checked-in
result is the structural catalog score only; agent-answer quality must be measured with an actual
client run and identified model/version.

## Runtime discovery choice

The measured BM25 transform is now available as an **opt-in** runtime setting,
`AESO_MCP_TOOL_DISCOVERY_MODE=compact`. The default remains `full`, preserving ordinary
client discovery and avoiding an extra tool call for straightforward market questions.
The benchmark above was recorded before the `search_aeso_tools` addition and should be
rerun for new releases; figures are historical measurements, not live size guarantees.


## Evaluation integrity

The external scorer also reports submitted-result and nonempty-answer coverage, exact tool-set
accuracy, tool selection precision and no-tool abstention accuracy. Missing answers receive no
literal safety credit, and duplicate or unknown case IDs are rejected. Tool arguments and
self-reported caveats remain structural evidence only; they do not prove that a model used correct
values or generated a factually grounded answer. Independent reviewers or fixture-derived numeric
assertions must establish correctness.

For real model comparisons, use the same frozen case set, observation fixtures, client settings,
model identifier, repeated trial count and scoring rules in both discovery modes. Retain tool-call
traces, timestamps, actual input/output token counts when provided by the client, wall-clock
latency and human-reviewed correctness. Report incomplete trials rather than imputing successes.
Do not use the canonical questions as both a tuning and independent holdout set.


## Credential-free routing holdout and workload evidence

The Research benchmarks workflow freezes actual MCP descriptions and BM25(question) candidates
on 12 separate routing cases, then compares a pinned Qwen2.5-0.5B-Instruct CPU checkpoint with
greedy decoding. Full mode receives all tool names/descriptions; compact receives the three pinned
tools plus BM25 matches. Neither receives the full JSON schemas, and compact discovery is performed
by the harness, not selected by the model. This measures route choice, not actual tool execution,
argument correctness, grounded numeric answers or deployed client behavior. Invalid or invented
names are failures, not successful abstentions. The manifest, raw outputs, exact checkpoint,
package versions, token counts and timing are retained as workflow artifacts.

A separate workflow job downloads the latest public monthly hourly CSD ZIP, normalizes it, writes
it to an ephemeral DuckDB/Parquet store and queries a bounded page. It records source SHA-256,
row counts, bytes, wall times and cumulative Linux process peak RSS. It never writes to a user's
production historical store. Public network availability and runner hardware influence these
measurements; do not confuse them with repeatable fixture-only results.


### Larger checkpoint comparison

The Stronger CPU model routing workflow compares pinned Qwen2.5 1.5B and 3B checkpoints using BF16 CPU inference. Strict JSON remains the primary metric; a separately reported diagnostic accepts exactly one enclosing JSON Markdown fence, but no other prose, duplicate or unknown tool names. This isolates format failures without retroactively changing the baseline. Compare full and compact within a model/dtype, not latency across models or runner hardware. The 12 routing cases are reused, not a fresh independent holdout for prompt tuning.


### Routing benchmark shard integrity

The original unsharded 1.5B/3B CPU workflows reached their 25-minute limits without
completing the twelve-question run. The larger-model workload is now split into four
disjoint three-question shards for each checkpoint, each with its own bounded job.
The aggregate job verifies eight artifacts, pinned revisions, identical full-case SHA256,
each shard identity and exactly-once coverage of every case in full and compact modes.
Incomplete or cancelled experiments cannot become a misleading complete score.

## Completed pinned-model evidence, November 28, 2026

The complete independently checked 12-question routing comparison in PR #31 showed:
Qwen2.5 1.5B exact routes 5/12 with full discovery versus 1/12 with compact
(strict parsing); Qwen2.5 3B 10/12 full versus 9/12 compact. Three of the
3B full-catalog successes were correct abstentions, compared with two for compact.
The 3B model used 37,465 versus 6,368 input tokens respectively.
These are description-only route selections, not full agent answers.

The separate 54-case synthetic benchmark (PR #37) produced no correct responses
for Qwen2.5 0.5B on 54 questions. On the same stratified 16-question subset,
1.5B achieved 3 correct end-to-end numerical answers and 3B achieved 3 correct
abstentions but no correct numerical answers. The native function-calling five-case
3B pilot (PR #38) achieved 1/5 correct end to end. Full benchmark scoring validates
all required model shards and case digests; it does not generalize to more capable
external clients or live AESO data. The discovery default remains unchanged.
