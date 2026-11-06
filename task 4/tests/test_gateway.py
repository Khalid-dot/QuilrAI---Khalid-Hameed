import asyncio
import hashlib
import json
import sqlite3

import pytest
from conftest import auth, gateway_client, success

from model_router.config import DEMO_KEYS, Provider


async def test_success_and_api_key_never_forwarded_or_stored(tmp_path):
    reached = []

    def provider(request):
        reached.append(request)
        assert "x-api-key" not in request.headers
        assert request.headers["authorization"] == "Bearer provider-only-secret"
        return success()

    async with gateway_client(
        tmp_path,
        provider,
        primary=Provider(
            "http://127.0.0.1:8005/v1/completions", "test-primary", "provider-only-secret"
        ),
    ) as (client, app):
        response = await client.post(
            "/v1/completions", json={"prompt": "Hi", "max_tokens": 10}, headers=auth()
        )
        assert response.status_code == 200
        result = response.json()
        assert result["text"] == "OK" and result["provider"] == "primary"
        assert result["usage"]["total_tokens"] == 3
        assert len(result["request_id"]) == 32
        with sqlite3.connect(app.state.limiter.path) as db:
            tenant = db.execute("SELECT tenant FROM token_events").fetchone()[0]
            assert tenant == hashlib.sha256(DEMO_KEYS[0].encode()).hexdigest()
        assert DEMO_KEYS[0] not in response.text and "provider-only-secret" not in response.text
    assert len(reached) == 1


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": "unknown"}, [("X-API-Key", DEMO_KEYS[0]), ("X-API-Key", DEMO_KEYS[1])]],
)
async def test_unauthorized_requests_never_reach_upstream(tmp_path, headers):
    reached = []
    async with gateway_client(tmp_path, lambda request: reached.append(request)) as (client, _):
        response = await client.post("/v1/completions", json={"prompt": "Hi"}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert reached == []


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"prompt": ""},
        {"prompt": 1},
        {"prompt": "test", "max_tokens": True},
        {"prompt": "test", "max_tokens": "10"},
        {"prompt": "test", "max_tokens": 0},
        {"prompt": "test", "max_tokens": 50_001},
        {"prompt": "test", "tenant": "forged"},
        {"prompt": "x" * 32_001},
    ],
)
async def test_bad_requests_do_not_reserve_or_dispatch(tmp_path, payload):
    reached = []
    async with gateway_client(tmp_path, lambda request: reached.append(request)) as (client, app):
        response = await client.post(
            "/v1/completions",
            content=json.dumps(payload),
            headers={**auth(), "Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert await app.state.limiter.used(hashlib.sha256(DEMO_KEYS[0].encode()).hexdigest()) == 0
    assert reached == []


async def test_bad_json_type_and_body_limit(tmp_path):
    reached = []
    async with gateway_client(tmp_path, lambda request: reached.append(request)) as (client, _):
        assert (
            await client.post("/v1/completions", content="{}", headers=auth())
        ).status_code == 415
        assert (
            await client.post(
                "/v1/completions",
                content="{",
                headers={**auth(), "Content-Type": "application/json"},
            )
        ).status_code == 400
        assert (
            await client.post("/v1/completions", json={"prompt": "x" * 70_000}, headers=auth())
        ).status_code == 413
    assert reached == []


async def test_local_rate_limit_and_tenant_isolation(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        return success(prompt_tokens=2, completion_tokens=8)

    async with gateway_client(tmp_path, provider, tokens_per_window=12) as (client, _):
        payload = {"prompt": "Hi", "max_tokens": 10}
        assert (
            await client.post("/v1/completions", json=payload, headers=auth())
        ).status_code == 200
        denied = await client.post("/v1/completions", json=payload, headers=auth())
        assert denied.status_code == 429
        assert int(denied.headers["retry-after"]) >= 1
        assert denied.json()["error"]["code"] == "rate_limit_exceeded"
        assert len(calls) == 1
        other = await client.post("/v1/completions", json=payload, headers=auth(DEMO_KEYS[1]))
        assert other.status_code == 200
        assert len(calls) == 2


async def test_request_larger_than_capacity_is_rejected(tmp_path):
    reached = []
    async with gateway_client(
        tmp_path, lambda request: reached.append(request), tokens_per_window=5
    ) as (client, _):
        response = await client.post(
            "/v1/completions", json={"prompt": "Hi", "max_tokens": 10}, headers=auth()
        )
    assert response.status_code == 422
    assert reached == []


async def test_concurrent_requests_reserve_before_dispatch(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def provider(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return success()

    async with gateway_client(tmp_path, provider, tokens_per_window=12) as (client, _):
        first = asyncio.create_task(
            client.post(
                "/v1/completions",
                json={"prompt": "Hi", "max_tokens": 10},
                headers=auth(),
            )
        )
        await entered.wait()
        try:
            others = await asyncio.gather(
                *[
                    client.post(
                        "/v1/completions", json={"prompt": "Hi", "max_tokens": 10}, headers=auth()
                    )
                    for _ in range(10)
                ]
            )
            assert all(response.status_code == 429 for response in others)
            assert len(calls) == 1
        finally:
            release.set()
            assert (await first).status_code == 200


async def test_storage_failure_stops_dispatch_and_is_sanitized(tmp_path, monkeypatch):
    reached = []
    async with gateway_client(tmp_path, lambda request: reached.append(request)) as (client, app):

        def broken():
            raise sqlite3.OperationalError("private path and database detail")

        monkeypatch.setattr(app.state.limiter, "_connect", broken)
        response = await client.post("/v1/completions", json={"prompt": "Hi"}, headers=auth())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert "private" not in response.text
    assert reached == []


async def test_unexpected_error_is_sanitized_and_has_request_id(tmp_path, monkeypatch):
    async with gateway_client(tmp_path, lambda request: success()) as (client, app):

        async def broken(*args):
            raise RuntimeError("private upstream stack and secrets")

        monkeypatch.setattr(app.state.router, "complete", broken)
        response = await client.post("/v1/completions", json={"prompt": "Hi"}, headers=auth())
    assert response.status_code == 500
    payload = response.json()["error"]
    assert payload["code"] == "internal_error"
    assert response.headers["x-request-id"] == payload["request_id"]
    assert "private" not in response.text and "Traceback" not in response.text
