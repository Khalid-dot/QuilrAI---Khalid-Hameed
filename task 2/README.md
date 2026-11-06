# Task 2 — MCP security gateway

Asynchronous HTTP/JSON-RPC reverse proxy with Bearer authentication and tool-level authorization. Includes a local mock MCP server.

## Setup

Requires Python 3.12+. Run inside `task 2`:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps --no-build-isolation -e .
```

## Run

The self-contained demo starts both servers on available loopback ports, checks forwarding and authorization, and stops the servers:

```sh
.venv/bin/python examples/demo.py
```

To run the services separately, use two terminals:

```sh
# Terminal 1 — downstream mock, port 8001:
.venv/bin/quilrai-mock-mcp

# Terminal 2 — gateway, port 8000:
.venv/bin/quilrai-gateway --demo
```

Both commands accept `--port`. Set `MCP_DOWNSTREAM_URL` when changing the downstream address.

| Demo Bearer token | Role |
| --- | --- |
| `demo-viewer-token` | viewer |
| `demo-admin-token` | admin |

Without `--demo`, set `GATEWAY_TOKENS` to a JSON object mapping opaque tokens to `admin` or `viewer`. The service refuses startup without valid tokens. Environment configuration takes precedence over demo tokens; `.env.example` lists the variables. Environment files are not loaded automatically.

## API and authorization

Send JSON-RPC requests to `POST http://127.0.0.1:8000/mcp`:

```sh
curl -i http://127.0.0.1:8000/mcp \
  -H 'Authorization: Bearer demo-viewer-token' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"admin_reset_key","arguments":{}}}'
```

The viewer receives HTTP 403:

```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32001,"message":"Unauthorized Tool Call"}}
```

Use `demo-admin-token` for success. The mock exposes `get_status` and `admin_reset_key`; both accept empty arguments.

- `tools/list` is forwarded unchanged, including administrative tool definitions.
- `tools/call` names beginning with the case-sensitive prefix `admin_` require the admin role.
- Denied calls never reach the downstream server.
- Roles come from the configured token map, not caller-supplied role claims.
- Authorized request bodies, IDs, and valid downstream JSON-RPC responses are preserved.

Client credentials and cookies are not forwarded. MCP session/protocol headers are preserved. The gateway reuses an asynchronous HTTP client, disables redirects, and enforces a five-second total downstream deadline.

## Errors and scope

| Condition | HTTP | JSON-RPC code |
| --- | --- | --- |
| Invalid or missing authentication | 401 | -32000 |
| Unauthorized administrative call | 403 | -32001 |
| Malformed JSON or duplicate members | 400 | -32700 |
| Invalid envelope or batch | 400 | -32600 |
| Invalid tool-call parameters | 400 | -32602 |
| Unsupported content type | 415 | -32600 |
| Request exceeds 1 MiB | 413 | -32600 |
| Downstream failure or invalid response | 502 | -32002 |
| Downstream timeout | 504 | -32003 |

Errors preserve valid parsed request IDs; authentication and pre-parse errors use null. Upstream HTTP failures are sanitized; downstream JSON-RPC errors returned with successful HTTP status are forwarded unchanged.

This is a POST JSON-RPC proxy. SSE, resumable streams, session deletion, OAuth discovery, and JSON-RPC batches are outside its scope. Lifecycle notifications receive an empty HTTP 202 after forwarding; tool requests require IDs. IDs may be strings, integers, or null. The mock binds to loopback and records received requests in memory for demo/test assertions.

## Verification

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pip check
```

Tests cover authorization before dispatch, transparent forwarding, malformed input, forged roles, concurrent requests, timeouts, and sanitized errors.

Source is in `src/mcp_gateway/`; tests are in `tests/`; the HTTP demonstration is in `examples/`.
