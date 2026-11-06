"""Real HTTP streaming demonstration with temporary loopback gateway/provider servers."""

import asyncio
import json
import socket
from contextlib import ExitStack
from time import perf_counter

import httpx
import uvicorn

from llm_guardrail.config import Settings
from llm_guardrail.gateway import create_app
from llm_guardrail.mock_provider import SAMPLE, create_mock_app
from llm_guardrail.sse import sse_data


def listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


async def demonstrate(url):
    chunks = []
    first_delta = None
    complete = False
    start = perf_counter()
    async with httpx.AsyncClient(trust_env=False) as client:
        async with client.stream(
            "POST", url, json={"prompt": "Show a sample response"}
        ) as response:
            response.raise_for_status()
            async for data in sse_data(response.aiter_bytes()):
                payload = json.loads(data)
                if "code" in payload:
                    raise RuntimeError("Demo stream failed")
                if "text" in payload:
                    if first_delta is None:
                        first_delta = perf_counter() - start
                    chunks.append(payload["text"])
                    print(payload["text"], end="", flush=True)
                elif payload.get("status") == "complete":
                    complete = True
    total = perf_counter() - start
    expected = (
        SAMPLE.replace("alex.morgan@example.com", "[REDACTED]")
        .replace(
            "123-45-6789",
            "[REDACTED]",
        )
        .replace("4111 1111 1111 1111", "[REDACTED]")
    )
    assert "".join(chunks) == expected
    assert complete and first_delta is not None
    assert first_delta < total and len(chunks) > 3
    print(f"\nFirst redacted text: {first_delta * 1000:.1f} ms")
    print(f"Total stream duration: {total * 1000:.1f} ms; emitted text events: {len(chunks)}")
    print("Demo passed: all three PII types redacted; text arrived before stream completion.")


async def main():
    with ExitStack() as stack:
        provider_socket = stack.enter_context(listener())
        gateway_socket = stack.enter_context(listener())
        url = f"http://127.0.0.1:{provider_socket.getsockname()[1]}/v1/chat/completions"
        provider = create_mock_app(delay=0.01, chunk_chars=3)
        gateway = create_app(Settings(provider_url=url))
        servers = [
            uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
            for app in (provider, gateway)
        ]
        tasks = [
            asyncio.create_task(server.serve(sockets=[sock]))
            for server, sock in zip(servers, (provider_socket, gateway_socket), strict=True)
        ]
        try:
            async with asyncio.timeout(10):
                while not all(server.started for server in servers):
                    for task in tasks:
                        if task.done():
                            task.result()
                            raise RuntimeError("Demo server exited before startup")
                    await asyncio.sleep(0.01)
            await demonstrate(f"http://127.0.0.1:{gateway_socket.getsockname()[1]}/v1/completions")
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
