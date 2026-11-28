# Independent agent-answer evaluation

This benchmark is separate from the original 5-case fixture pilot and from the routing-only
70-case structural and 12-case model experiments. It tests actual `get_pool_prices` and
`get_load` FastMCP calls, including genuine tool schemas, argument validation, structured
results and provenance, while replacing the upstream data source with a fully deterministic
48-hour synthetic market dataset. No external market credentials are involved.

## Fixed evaluation design

- 54 independently generated questions: 48 numerical and six no-tool/unsupported requests.
  The categories are point Pool Price (10), point AIL (8), mean price (6), maximum price (6),
  minimum price (4), maximum AIL (4), load-weighted price (6), price at peak load (4), and
  unsupported requests (6).
- The raw hourly data spans November 2–4, 2025 UTC and includes the Alberta fall DST
  transition, negative prices, zero prices, a high price and varying AIL.
- The answer oracle computes from the fixed arrays before MCP execution; the model sees
  only each question, tool schemas and (if it succeeds) actual structured tool responses.
- Only a maximum of two tools and six-hour UTC ranges can execute. No SQL, HTTP requests,
  arbitrary tools, bid submission, financial action or private participant data are allowed.
- The formal accepted format is either a bare JSON object or one exact enclosing JSON
  Markdown fence. Freeform prose, duplicate calls, unauthorized actions and fabricated
  numerical units are failures. Numerical tolerance is ±0.025 in the correct unit.
- Detailed metrics distinguish valid output, strict JSON, correct routing, correct
  arguments, actual tool execution, correct arithmetic, abstention and total correctness.
- Qwen2.5 0.5B is evaluated on all 54 cases. A fixed stratified 16-case subset is
  evaluated by the 1.5B and 3B models. Only the shared 16-case scores permit a fair
  cross-model comparison. Checkpoint revisions are pinned; CPU inference is greedy.
- Each job scores a disjoint shard. The aggregate job rejects missing shards, repeated
  cases, wrong checkpoint revisions and case-hash mismatches. It never treats a
  partially cancelled benchmark as a complete accuracy result.

Artifacts retain raw model outputs and observed MCP responses for reproducibility. These
synthetic cases test whether models can use accurate market tools and arithmetic. They do
**not** establish performance against real AESO source revisions, ambiguous business questions,
a native remote-client tool-calling API, or a production trading workflow. The default MCP
discovery mode must not change based solely on these results.
