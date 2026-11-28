# Qwen2.5 native function-calling pilot

The original five-case 3B fixture pilot used plain natural language prompting to request
JSON tool calls. This separate experiment passes actual FastMCP schemas to the model's
own checkpoint-provided `tokenizer.apply_chat_template(..., tools=...)` in Transformers.
It accepts Qwen2.5's native `<tool_call>` tool envelopes (and the documented older
`<|tool_call_start|>` dialect), handles both JSON objects and JSON-string arguments,
and executes only the two existing allowlisted read-only market-data fixture tools.

The same five synthetic questions and fixed values are used, so the pilot isolates the
interaction template rather than changing the evaluation target. It scores native call
parsing, tool selection, exact timestamp arguments, execution, independently checked
numeric answers and unsupported-question abstention. No external AESO API keys, arbitrary
URLs, production writes, or trading functionality are accessible.

The result is still only a **five-question synthetic pilot**; it is not a validated
external ChatGPT/Codex client or a substitute for 54-case independent accuracy.
Source: [Qwen official function-calling documentation](https://qwen.readthedocs.io/en/v2.0/framework/function_call.html).
