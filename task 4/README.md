# Task 4 — Token limiter and fallback router

Asynchronous completion router with an on-disk SQLite token limiter: **50,000 tokens per rolling 60 seconds per tenant API key**. Primary HTTP 429 or a **3,000 ms attempt timeout** triggers secondary-provider fallback.

## Setup

Requires Python 3.12+. Run inside `task 4`:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m model_router.prepare
```

The preparation command downloads `cl100k_base` tokenizer data once and caches it in `.cache/tiktoken/`. `TIKTOKEN_CACHE_DIR` overrides the location. Prepare any additional configured encoding before startup. Request handling uses cached tokenizers.

## Run

The self-contained demo starts three servers on available loopback ports and uses a temporary on-disk database. It verifies primary success, both fallback triggers, quota denial, tenant isolation, persistence, and sanitized backup failure. The timeout scenario takes at least three seconds.

```sh
.venv/bin/python examples/demo.py
```

To run separately, use three terminals:

```sh
# Terminal 1 — primary, port 8005:
.venv/bin/quilrai-router-mock --role primary

# Terminal 2 — secondary, port 8006:
.venv/bin/quilrai-router-mock --role secondary

# Terminal 3 — gateway, port 8004:
.venv/bin/quilrai-router --demo
```

All commands accept `--port`. The gateway accepts `--db`; the default database is `data/router.sqlite3`, relative to the launch directory, and persists across restarts.

```sh
curl -i http://127.0.0.1:8004/v1/completions \
  -H 'X-API-Key: demo-tenant-a-key' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Hello","max_tokens":32}'
```

`--demo` enables `demo-tenant-a-key` and `demo-tenant-b-key`. Mock prompts `[429]`, `[timeout]`, `[both-fail]`, and `[error]` exercise rejection, timeout, backup failure, and primary HTTP 500 respectively.

## API and configuration

`POST /v1/completions` requires exactly one valid `X-API-Key`. The prompt must contain 1–32,000 characters. Optional `max_tokens` is an integer from 1–50,000, default 256. Unknown fields are rejected; encoded requests are limited to 64 KiB.

Successful responses contain `request_id`, `text`, `usage`, `provider` (`primary` or `secondary`), and the configured `model`.

The upstream adapter sends a non-streaming completion request with `model`, `prompt`, `max_tokens`, and `stream: false`. It requires one `choices[0].text` plus consistent integer `usage.prompt_tokens`, `usage.completion_tokens`, and `usage.total_tokens`. Invalid usage, output beyond the requested cap, and responses larger than 1 MiB are rejected.

Configuration variables are listed in `.env.example`:

| Variable | Purpose |
| --- | --- |
| `TENANT_API_KEYS` | JSON list of accepted tenant keys; required without `--demo` |
| `ROUTER_DATABASE` | On-disk SQLite path |
| `PRIMARY_URL`, `SECONDARY_URL` | Compatible non-streaming completion endpoints |
| `PRIMARY_MODEL`, `SECONDARY_MODEL` | Provider model names |
| `PRIMARY_ENCODING`, `SECONDARY_ENCODING` | Tokenizer encodings; default `cl100k_base` |
| `PRIMARY_API_KEY`, `SECONDARY_API_KEY` | Optional server-side provider credentials |

Environment files are not loaded automatically. Configured tenant keys take precedence over demo keys. Tenant identity is the SHA-256 digest of the authenticated key; SQLite stores token events and hashes, not prompts or raw credentials. Tenant keys are not forwarded upstream. Provider addresses are server-controlled, redirects are disabled, and CLIs bind to loopback.

## Accounting and concurrency

Before each attempt, reserve the endpoint's prompt-token count plus `max_tokens`. Token counts use tiktoken with the configured encoding. This raw-prompt API has no chat-template overhead; the encoding must match the provider model.

Admission uses `BEGIN IMMEDIATE` to atomically evict expired events, sum tenant usage, check capacity, and persist a reservation before dispatch. The active window is `(now - 60 seconds, now]`, measured from attempt admission. Settlement preserves that timestamp. WAL mode, indexed timestamps, and a five-second busy timeout support independent connections and processes sharing one database file. SQLite operations and tokenization run in worker threads.

| Outcome | Token charge |
| --- | --- |
| Success with valid usage | Reported total tokens |
| HTTP 429 rejection | Zero; reservation released |
| Timeout or other uncertain provider failure | Full reservation retained |
| Unresolved cancellation or process crash | Reservation retained until expiry |

Settlement is idempotent. Expired events are evicted on admission and excluded from usage reads immediately. Unresolved reservations survive restarts. If reported usage exceeds the predicted reservation, the actual overage is recorded and subsequent admissions are restricted. Accurate admission bounds depend on the provider's correct tokenizer and output cap.

A timed-out primary may have consumed tokens, so fallback needs a separate reservation. Insufficient remaining quota returns local HTTP 429 without dispatching the backup. `Retry-After` reports when enough accounted tokens expire; settlement may free capacity sooner. SQLite timestamps use wall-clock time and assume synchronized host clocks.

## Fallback and errors

The primary has a three-second total attempt deadline, including headers and body reads. Timeout cancels and closes the primary attempt before fallback. The secondary has its own three-second deadline and is attempted once. Other primary failures return an error without fallback. Cancellation propagates without retry; late primary results cannot overwrite backup results. Tokenization and SQLite admission are outside provider deadlines.

Gateway errors have a consistent shape and return the request ID in `X-Request-Id`:

```json
{"error":{"code":"rate_limit_exceeded","message":"Tenant token rate limit exceeded","request_id":"generated-request-id"}}
```

| HTTP | Code |
| --- | --- |
| 400 / 413 / 415 | `invalid_request` |
| 401 | `unauthorized` |
| 422 | `request_exceeds_quota`: one reservation exceeds tenant capacity |
| 429 | `rate_limit_exceeded`: local tenant quota exhausted |
| 502 | `provider_unavailable` |
| 503 | `storage_unavailable`: accounting failed; dispatch denied |
| 504 | `provider_timeout`: secondary timeout |
| 500 | `internal_error` |

Upstream bodies, SQL errors, internal URLs, credentials, and stack traces are excluded from error responses. A secondary HTTP 429 returns `provider_unavailable`, distinguishing provider exhaustion from local tenant limits.

## Verification and reuse

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pip check
```

Tests cover the actual three-second deadline, process/connection concurrency, exact window boundaries, persistence, tenant isolation, reconciliation, cancellation, fallback, and error sanitization.

`ModelRouter.complete(tenant, prompt, max_tokens)` accepts validated inputs with an initialized `SQLiteTokenLimiter` and shared asynchronous HTTP client. The HTTP wrapper supplies authenticated tenant hashes. This module handles non-streaming completions independently of Task 3's streaming guardrail.

Source is in `src/model_router/`; tests are in `tests/`; the HTTP demonstration is in `examples/`.
