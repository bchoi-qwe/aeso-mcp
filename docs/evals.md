# Agent-use evaluation

The repository includes 50 canonical user questions in `tests/evals/cases.json`. Cases cover
current conditions, source selection, historical generation, store operations, research
analytics, operating reserves, timestamp/finality semantics, and safe refusal boundaries.

## What CI verifies

- every case has a unique ID and a non-empty question;
- every expected tool exists on the live MCP surface;
- every case identifies expected argument fields and authoritative datasets;
- every case identifies prohibited interpretations, required caveats, and any numerical
  relationships that should hold;
- all roadmap tool families have canonical coverage; and
- the generated MCP catalog is current.

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
