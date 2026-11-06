# Task 3 — Streaming PII guardrail

Asynchronous LLM gateway that redacts emails, SSNs, and card numbers across provider deltas and streams safe text through SSE. Includes a mock provider, HTTP demonstration, and memory benchmark.

## Setup

Requires Python 3.12+. Run inside `task 3`:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps --no-build-isolation -e .
```

## Run

The self-contained demo starts both servers on available loopback ports, displays redacted text as it arrives, measures first-text latency, and stops the servers:

```sh
.venv/bin/python examples/demo.py
```

To run separately, use two terminals:

```sh
# Terminal 1 — provider, port 8003:
.venv/bin/quilrai-mock-llm

# Terminal 2 — gateway, port 8002:
.venv/bin/quilrai-guardrail
```

Both commands accept `--port`. The mock emits a fixed synthetic response in three-character deltas.

```sh
curl -N http://127.0.0.1:8002/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Show a sample response","max_tokens":256}'
```

## API and provider configuration

`POST /v1/completions` accepts a prompt of 1–32,000 characters and an optional integer `max_tokens` from 1–4096, default 256. Unknown fields are rejected; encoded requests are limited to 64 KiB.

Configure `LLM_PROVIDER_URL`, `LLM_MODEL`, and optional `LLM_API_KEY` for a compatible provider. Defaults target the local mock. `.env.example` lists the variables; environment files are not loaded automatically. Provider configuration and credentials are server-controlled.

The upstream adapter sends `model`, `messages`, `max_tokens`, `stream: true`, and `n: 1`. It expects SSE with one `choices[0].delta.content` string and a final `[DONE]` marker. Role-only and usage-only events are ignored. Multiple choices and non-text deltas are rejected.

Client responses use this SSE contract:

```text
event: delta
data: {"text": "Contact [REDACTED] for help."}

event: done
data: {"status": "complete"}

```

Concatenate `delta.text` to render the response. A midstream failure emits a terminal `error` event with `code: provider_stream_error` and a sanitized message, without a `done` event. Pending text is discarded. Before streaming starts, invalid requests return HTTP 400/413/415, provider failures return 502, and setup timeouts return 504; JSON errors use `{"error":{"code":"...","message":"..."}}`.

The provider client has a ten-second inactivity timeout and a ten-second response-header deadline. Streams have no total duration cap. Responses disable caching and request proxy buffering to be disabled.

## Redaction and memory bounds

| Pattern | Supported form |
| --- | --- |
| Email | Common unquoted ASCII local parts and dotted ASCII domains, including plus addressing and subdomains |
| SSN | `DDD-DD-DDDD`, bounded by non-digits |
| Card | 13–19 ASCII digits, with an optional single space or hyphen between digits |

Matches become `[REDACTED]`. SSNs and cards are detected by shape, without issuance-range or Luhn validation. Quoted/internationalized emails, obfuscated addresses, and other numeric separators are outside the supported patterns. Matching may over-redact email-like strings and numeric identifiers.

The redactor holds email atoms and numeric runs until their boundaries. Ordinary words are released at delimiters; unresolved prefixes are never released on a timer. This introduces candidate-boundary latency without buffering the complete response.

| Buffer | Limit |
| --- | --- |
| Pending email atom | 320 characters |
| Pending numeric run | 128 characters |
| Combined pending text | 448 characters per request |
| Provider SSE event | 64 KiB |

An oversized candidate produces one `[REDACTED]`; its remainder is discarded through the next boundary. This may remove a long harmless identifier or spaces within a numeric run. Regex operations apply only to bounded candidates. Memory depends on one bounded provider event and fixed candidate buffers, not completion length.

The SSE parser handles arbitrary byte boundaries, split UTF-8, multiline data, comments, BOM, and CR/LF/CRLF. Only `[DONE]` triggers the final text flush. Truncation, errors, cancellation, and disconnect discard unresolved text and close the upstream response. Pull-based iteration applies backpressure, and each request has independent redactor state.

## Verification

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pip check
.venv/bin/python examples/benchmark.py
```

Tests cover every two-chunk split of sample PII, one-character deltas, randomized partitions, memory bounds, framing, concurrent streams, backpressure, cancellation, and sanitized failures. The benchmark processes 1.1 million characters without retaining the complete input or output and reports pending-state and traced Python memory usage.

Source is in `src/llm_guardrail/`; tests are in `tests/`; demos and benchmarks are in `examples/`.
