import asyncio
from dataclasses import replace

import httpx
import pytest
from conftest import CharacterCounter, success

from model_router.config import DEMO_KEYS, Settings
from model_router.errors import GatewayError
from model_router.limiter import SQLiteTokenLimiter
from model_router.router import ModelRouter, parse_completion


async def make_router(tmp_path, handler, **overrides):
    settings = replace(
        Settings(api_keys=DEMO_KEYS, database=tmp_path / "quota.sqlite3"), **overrides
    )
    limiter = SQLiteTokenLimiter(settings.database, limit=settings.tokens_per_window)
    await limiter.initialize()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return ModelRouter(settings, limiter, client, counters=(CharacterCounter(), CharacterCounter()))


async def test_primary_success_does_not_call_secondary_and_reconciles(tmp_path):
    reached = []

    def handler(request):
        reached.append(request.url.port)
        return success(prompt_tokens=2, completion_tokens=1)

    router = await make_router(tmp_path, handler)
    async with router.client:
        result = await router.complete("tenant", "Hi", 10)
    assert reached == [8005]
    assert result.provider == "primary" and result.text == "OK"
    assert await router.limiter.used("tenant") == 3


async def test_429_releases_primary_reservation_then_calls_backup(tmp_path):
    reached = []

    def handler(request):
        reached.append(request.url.port)
        return (
            httpx.Response(429, text="private details") if request.url.port == 8005 else success()
        )

    router = await make_router(tmp_path, handler, tokens_per_window=12)
    async with router.client:
        result = await router.complete("tenant", "Hi", 10)
    assert reached == [8005, 8006]
    assert result.provider == "secondary"
    assert await router.limiter.used("tenant") == 3


async def test_timeout_cancels_primary_retains_unknown_usage_and_falls_back(tmp_path):
    cancelled = asyncio.Event()
    reached = []

    async def handler(request):
        reached.append(request.url.port)
        if request.url.port == 8005:
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.set()
        return success()

    router = await make_router(tmp_path, handler, primary_timeout_seconds=0.02)
    async with router.client:
        result = await router.complete("tenant", "Hi", 10)
    assert cancelled.is_set()
    assert reached == [8005, 8006]
    assert result.provider == "secondary"
    assert await router.limiter.used("tenant") == 12 + 3


async def test_exact_assessment_deadline_is_3000ms(tmp_path):
    assert Settings(api_keys=DEMO_KEYS).primary_timeout_seconds == 3.0
    cancelled = False

    async def handler(request):
        nonlocal cancelled
        if request.url.port == 8005:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled = True
        return success()

    router = await make_router(tmp_path, handler)
    started = asyncio.get_running_loop().time()
    async with router.client:
        result = await router.complete("tenant", "Hi", 10)
    elapsed = asyncio.get_running_loop().time() - started
    assert 2.9 <= elapsed < 4.5
    assert result.provider == "secondary" and cancelled


async def test_no_backup_dispatch_when_its_reservation_would_exceed_quota(tmp_path):
    reached = []

    async def handler(request):
        reached.append(request.url.port)
        await asyncio.sleep(1)
        return success()

    router = await make_router(
        tmp_path, handler, tokens_per_window=20, primary_timeout_seconds=0.01
    )
    async with router.client:
        with pytest.raises(GatewayError) as error:
            await router.complete("tenant", "Hi", 10)
    assert error.value.status == 429
    assert reached == [8005]
    assert await router.limiter.used("tenant") == 12


@pytest.mark.parametrize("kind", ["500", "connect", "invalid_json", "missing_usage"])
async def test_other_primary_errors_are_sanitized_without_unspecified_fallback(tmp_path, kind):
    reached = []

    def handler(request):
        reached.append(request.url.port)
        if kind == "connect":
            raise httpx.ConnectError("private URL and credential", request=request)
        if kind == "500":
            return httpx.Response(500, text="private stack trace")
        if kind == "invalid_json":
            return httpx.Response(200, text="private HTML")
        return httpx.Response(200, json={"choices": [{"text": "OK"}]})

    router = await make_router(tmp_path, handler)
    async with router.client:
        with pytest.raises(GatewayError) as error:
            await router.complete("tenant", "Hi", 10)
    assert error.value.status == 502
    assert "private" not in error.value.message
    assert reached == [8005]
    assert await router.limiter.used("tenant") == 12


@pytest.mark.parametrize("backup", [429, 500, "timeout"])
async def test_backup_failure_does_not_retry_indefinitely(tmp_path, backup):
    reached = []

    async def handler(request):
        reached.append(request.url.port)
        if request.url.port == 8005:
            return httpx.Response(429)
        if backup == "timeout":
            await asyncio.sleep(1)
        return httpx.Response(backup if isinstance(backup, int) else 500)

    router = await make_router(tmp_path, handler, secondary_timeout_seconds=0.01)
    async with router.client:
        with pytest.raises(GatewayError) as error:
            await router.complete("tenant", "Hi", 10)
    assert error.value.status == (504 if backup == "timeout" else 502)
    assert reached == [8005, 8006]
    assert await router.limiter.used("tenant") == (0 if backup == 429 else 12)


async def test_cancellation_keeps_persisted_reservation(tmp_path):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    router = await make_router(tmp_path, handler)
    async with router.client:
        task = asyncio.create_task(router.complete("tenant", "Hi", 10))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()
    assert await router.limiter.used("tenant") == 12


@pytest.mark.parametrize(
    "body",
    [
        b"null",
        b"[]",
        b"{}",
        b'{"choices":[],"usage":{}}',
        b'{"choices":[{"text":"OK"}],"usage":{"prompt_tokens":true,"completion_tokens":1,"total_tokens":2}}',
        b'{"choices":[{"text":"OK"}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":4}}',
        b'{"choices":[{"text":"OK"}],"usage":{"prompt_tokens":2,"completion_tokens":99,"total_tokens":101}}',
    ],
)
def test_malformed_completion_usage(body):
    from model_router.router import AttemptFailure

    with pytest.raises(AttemptFailure):
        parse_completion(body, 10)
