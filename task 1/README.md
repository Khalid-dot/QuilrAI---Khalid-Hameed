# Task 1 — Customer MCP server

Python MCP server using the official SDK, strict Pydantic validation, and stdio transport. Customer records and refunds use an in-memory mock backend; refunds have no payment side effects.

## Setup

Requires Python 3.12+. Run inside `task 1`:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
```

## Run

```sh
# Start the stdio server:
.venv/bin/quilrai-mcp

# Run the official SDK client demonstration:
.venv/bin/python examples/demo_client.py
```

The server waits for MCP messages on stdin. Stdout contains only protocol messages; diagnostics go to stderr. The demonstration starts its own server and exercises discovery, lookup, refund, and validation errors.

For an MCP host, set the command to the absolute path of `.venv/bin/python` and the arguments to `["-m", "quilrai_mcp"]` after installation.

## Tools

| Tool | Input | Result |
| --- | --- | --- |
| `get_customer_record` | `customer_id` | Mock customer record |
| `trigger_refund` | `customer_id`, `amount`, `reason` | Unique simulated refund receipt |

Validation rules:

- Customer IDs contain `CUST-` followed by exactly five ASCII digits.
- Amounts are finite, positive JSON numbers. Strings and booleans are rejected; JSON integers are accepted.
- Reasons contain at least 10 characters.
- Missing and extra fields are rejected. Values are not trimmed or coerced.

Mock records are available for `CUST-00001` and `CUST-00002`. Other valid IDs return a tool execution error.

## Errors and SDK behavior

| Condition | Response |
| --- | --- |
| Invalid tool arguments | JSON-RPC `-32602`, `Invalid params` |
| Unknown tool | JSON-RPC `-32602`, `Unknown tool` |
| Missing customer | Tool result with `isError: true` |
| Unexpected backend failure | JSON-RPC `-32603`, `Internal error` |
| Recognized MCP method without a handler | JSON-RPC `-32601` |

The handler registers through the SDK's low-level `request_handlers` mapping so `McpError` becomes a protocol error. The SDK 1.x tool decorator instead converts exceptions into tool results.

In pinned SDK 1.30.0, entirely unknown method names are rejected by the request parser with `-32602`. Parsing, framing, initialization, and transport lifecycle remain SDK-managed.

## Verification

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pip check
```

Tests cover strict schemas, edge cases, exact wire error codes, request IDs, stdout isolation, stderr logging, SDK client interoperability, and sanitized backend failures.

Source is in `src/quilrai_mcp/`; tests are in `tests/`; the client demonstration is in `examples/`.
