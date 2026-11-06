"""Deterministic chat-completion SSE provider, with intentionally fragmented PII."""

import asyncio
import json

from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

SAMPLE = (
    "Hello! Contact alex.morgan@example.com for help. "
    "SSN: 123-45-6789. Card: 4111 1111 1111 1111. "
    "Your request is being processed safely. Thanks!"
)


def provider_event(text: str) -> bytes:
    payload = {"choices": [{"index": 0, "delta": {"content": text}}]}
    return ("data: " + json.dumps(payload) + "\n\n").encode()


def create_mock_app(*, delay: float = 0.01, chunk_chars: int = 3):
    if chunk_chars <= 0 or delay < 0:
        raise ValueError("Invalid mock stream parameters")

    async def complete(request):
        async def stream():
            for index in range(0, len(SAMPLE), chunk_chars):
                await asyncio.sleep(delay)
                yield provider_event(SAMPLE[index : index + chunk_chars])
            yield b"data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return Starlette(routes=[Route("/v1/chat/completions", complete, methods=["POST"])])
