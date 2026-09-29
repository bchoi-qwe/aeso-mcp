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

On the checked-in catalog, the full listing contains 63 tools and serializes to 356,427 characters
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
