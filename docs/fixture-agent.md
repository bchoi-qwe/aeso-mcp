# Bounded model-execution pilot

The Fixture agent benchmark compares a pinned Qwen2.5-3B checkpoint on five synthetic
questions. It exposes the actual two MCP tool schemas (Pool Price and AIL), accepts generated
arguments only within a three-hour synthetic fixture, invokes the actual FastMCP Client and
market services, and returns real structured MCP responses to the model for numeric answering.
No credentials, live calls, arbitrary tool names, or arbitrary date ranges are permitted.

The independently defined fixture has hourly prices -10, 0 and 40 CAD/MWh and AIL 8000,
8500 and 9000 MW. Metrics distinguish correct route, exact UTC range, successful execution,
correct numeric answer and fully correct end-to-end response. Unsupported requests are scored
on explicit abstention. One exact JSON fence is accepted for this pilot; arbitrary prose is not.
No question or expected answer is sent to the model as an example. Raw model responses,
actual tool results, model revision and token counts are retained in the workflow artifact.
This is a small synthetic regression pilot, not a broad held-out benchmark, native tool-calling
client comparison or validation of real market numerical accuracy. Its results must not change
the production discovery default on their own.
