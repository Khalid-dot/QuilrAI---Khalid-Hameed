"""Three real HTTP servers, on-disk SQLite, actual 3000 ms timeout, and quota rejection."""

import asyncio
import hashlib
import socket
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import httpx
import uvicorn

from model_router.config import DEMO_KEYS, Provider, Settings
from model_router.gateway import create_app
from model_router.limiter import SQLiteTokenLimiter
from model_router.mock_provider import create_mock_app
from model_router.tokens import TokenCounter


def listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


async def demonstrate(url, app, primary, database):
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        for prompt, expected in (
            ("Hello", "primary"),
            ("[429]", "secondary"),
            ("[timeout]", "secondary"),
        ):
            started = perf_counter()
            response = await client.post(
                url, json={"prompt": prompt, "max_tokens": 32}, headers={"X-API-Key": DEMO_KEYS[0]}
            )
            elapsed = perf_counter() - started
            response.raise_for_status()
            result = response.json()
            assert result["provider"] == expected
            if prompt == "[timeout]":
                assert elapsed >= 2.9
            print(
                f"{prompt}: {result['provider']}, {elapsed * 1000:.0f} ms, usage={result['usage']}"
            )

        tenant_b = hashlib.sha256(DEMO_KEYS[1].encode()).hexdigest()
        await app.state.limiter.reserve(tenant_b, 49_999)
        before = len(primary.state.calls)
        response = await client.post(
            url, json={"prompt": "Hi", "max_tokens": 10}, headers={"X-API-Key": DEMO_KEYS[1]}
        )
        assert response.status_code == 429
        assert len(primary.state.calls) == before
        print(
            "Tenant B quota exhausted:",
            response.json()["error"]["code"],
            "Retry-After:",
            response.headers["retry-after"],
        )
        response = await client.post(
            url, json={"prompt": "Hi", "max_tokens": 10}, headers={"X-API-Key": DEMO_KEYS[0]}
        )
        assert response.status_code == 200
        print("Tenant A remains independent: HTTP 200")

        response = await client.post(
            url,
            json={"prompt": "[both-fail]", "max_tokens": 10},
            headers={"X-API-Key": DEMO_KEYS[0]},
        )
        assert response.status_code == 502 and "private" not in response.text
        print("Backup failure sanitized:", response.json()["error"]["code"])

    reopened = SQLiteTokenLimiter(database)
    await reopened.initialize()
    assert await reopened.used(tenant_b) == 49_999
    print("Reopened on-disk database: tenant B still has 49,999 tokens accounted")
    print("Demo passed: primary, 429/3000 ms fallback, quota, isolation, persistence, errors.")


async def main():
    counter = TokenCounter()
    with ExitStack() as stack:
        directory = stack.enter_context(TemporaryDirectory(prefix="quilrai-router-demo-"))
        sockets = [stack.enter_context(listener()) for _ in range(3)]
        primary = create_mock_app("primary", counter=counter)
        secondary = create_mock_app("secondary", counter=counter)
        database = Path(directory) / "router.sqlite3"
        settings = Settings(
            api_keys=DEMO_KEYS,
            database=database,
            primary=Provider(
                f"http://127.0.0.1:{sockets[0].getsockname()[1]}/v1/completions", "mock-primary"
            ),
            secondary=Provider(
                f"http://127.0.0.1:{sockets[1].getsockname()[1]}/v1/completions", "mock-secondary"
            ),
        )
        gateway = create_app(settings, counters=(counter, counter))
        servers = [
            uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
            for app in (primary, secondary, gateway)
        ]
        tasks = [
            asyncio.create_task(server.serve(sockets=[sock]))
            for server, sock in zip(servers, sockets, strict=True)
        ]
        try:
            async with asyncio.timeout(10):
                while not all(server.started for server in servers):
                    for task in tasks:
                        if task.done():
                            task.result()
                            raise RuntimeError("Demo server exited before startup")
                    await asyncio.sleep(0.01)
            await demonstrate(
                f"http://127.0.0.1:{sockets[2].getsockname()[1]}/v1/completions",
                gateway,
                primary,
                database,
            )
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
