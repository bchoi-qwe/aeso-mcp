# Model and agent evaluation findings — November 28, 2026

Scope: reproducible CPU model experiments in GitHub Actions against the
`aeso-mcp` tool catalog and strictly bounded synthetic FastMCP fixtures.
These are **not** accuracy claims for a deployed ChatGPT, Claude, Codex or other
remote client, nor do they demonstrate real AESO numerical-answer accuracy.

## Independent numerical-answer benchmark (merged PR #37)

- Fixed 48-hour synthetic Pool Price and AIL arrays with independent arithmetic oracle.
  54 questions total: 48 numerical questions plus six unsupported/no-tool requests.
  Qwen2.5-0.5B-Instruct was evaluated on all 54. Qwen2.5-1.5B and 3B were
  evaluated on the **same 16-case stratified subset** (13 numeric and three
  unsupported). All runs pinned model revisions and used greedy CPU inference.
- Model-selected JSON tools were executed through real FastMCP `get_pool_prices`
  and `get_load` interfaces only after strict shape and time-bound validation.
  Results were graded on correct tool set, argument range, successful execution,
  correct value and unit, and abstention.
- All 18 model shards, aggregate evidence integrity checks, fixture preflight,
  locked dependency audit, MCP conformance, Docker and CI passed.
  [PR #37](https://github.com/bchoi-qwe/aeso-mcp/pull/37) and its
  [Actions workflow](https://github.com/bchoi-qwe/aeso-mcp/actions/workflows/independent-agent-accuracy.yml)
  contain immutable per-shard raw traces and the verified aggregate report.

| Model | Evaluated | Valid structured selections | Correct routes | Correct arguments | Tool executions | Correct numeric answers | Correct abstentions | End-to-end correct |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen2.5 0.5B | 54 | 0 | 0 | 0 | 0 | 0 | 0 | **0/54** |
| Qwen2.5 1.5B | 16 | 10 | 9 | 8 | 9 | 3 | 0 | **3/16** |
| Qwen2.5 3B | 16 | 3 | 3 | 0 | 0 | 0 | 3 | **3/16** |

The larger models' shared 16-question scores equal the trivial
**always-abstain baseline of 3/16**, but for different reasons:
1.5B solved three numerical cases and failed all abstentions; 3B correctly
abstained three times but executed no valid numerical calls. Neither passed
a sufficient reliability threshold for an autonomous market-data agent.

## Five-case tool-execution pilots

The [initial manually prompted 3B fixture pilot](https://github.com/bchoi-qwe/aeso-mcp/pull/33)
scored **4/5 fully correct** (three numerical answers plus a correct abstention).
The failure was a missed mean-price tool selection. It used the same two bounded,
actual FastMCP tools but a different prompt with a simple one-tool decision.

The [native Qwen3B function-call pilot](https://github.com/bchoi-qwe/aeso-mcp/pull/38)
used the checkpoint's `apply_chat_template(..., tools=...)` and parsed native
`<tool_call>` envelopes rather than requesting JSON manually. All three
workflow shards and all CI gates passed.

| Native function-call metric | Result |
| --- | ---: |
| Valid tool-call syntax or refusal | 5/5 |
| Correct tool selection or abstention | 4/5 |
| Correct nested MCP argument shape | 2/4 numerical cases |
| Actual MCP tool executions | 2/4 numerical cases |
| Correct numerical answers | 1/4 |
| **End-to-end correctness** | **1/5** |

Example: the model chose the correct Pool Price tool but sometimes emitted
flat `start`/`end` fields rather than the tool's required nested
`request` object. This is a model-generated argument failure, **not evidence
that the typed MCP schema is invalid**. The pilots are too small for a claim
that native Qwen calling or manual JSON is generally better.

## Tool-routing-only catalog experiment

The independent [12-question routing experiment](https://github.com/bchoi-qwe/aeso-mcp/pull/31)
measures only tool name choice from published descriptions, not arguments or
numerical answers. It compares the whole tool catalog with compact BM25
discovery + pinned tools. The complete Qwen2.5-3B shards returned:

| Qwen2.5-3B routing measure | Full catalog | Compact |
| --- | ---: | ---: |
| Exact tool-set routes | **10/12** | 9/12 |
| Valid structured tool selections | 12/12 | 11/12 |
| Correct no-tool abstentions | 3/3 | 2/3 |
| Input tokens | 37,465 | 6,368 |

Compact discovery reduced prompt input tokens ~83%, but was slightly less
accurate in this limited experiment. **Retain full catalog as the default.**
The same workflow's 1.5B aggregate is **pending complete shard evidence**;
do not report an incomplete 1.5B total as a validated score. Review the
completed aggregate report in PR #31 before revising this paragraph.

## Interpretation and next acceptance gate

- These results differ materially by prompt, task scope, checkpoint size,
  output format and agent loop. A five-case pilot is not a substitute for the
  larger independent benchmark. None show that the upstream market data,
  deterministic calculations or MCP protocol are unreliable.
- The existing MCP should remain a **typed read-only evidence interface**,
  not absorb an embedded LLM. Preserve exact market timestamps, source
  provenance, publication cutoffs, explicit missingness and audited timezones.
- Before selecting compact discovery or releasing a production agent:
  compare one or more current strong tool-calling models through a real,
  identified MCP client with frozen case IDs, fixed model revisions,
  at least two independent trials, exact arguments, actual source/tool
  responses, answer correctness, abstention, caveats, latency, calls and
  token usage. Score full versus compact on the same held-out cases without
  tuning on the test set. Include independent real-AESO numerical checks
  with publication timestamps and missing-data handling.
- The new REM production API remains unverified separately; it is
  tracked in [issue #39](https://github.com/bchoi-qwe/aeso-mcp/issues/39).
  Do not use the legacy authenticated APIM as if it were a new REM endpoint.

**No model was shipped, no discovery default changed, and no market orders
were submitted as part of these experiments.**
