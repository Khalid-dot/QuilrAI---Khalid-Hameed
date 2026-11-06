"""Mock completion endpoints with deliberate 429, timeout, and failure scenarios."""

import asyncio

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from model_router.tokens import TokenCounter


def create_mock_app(role: str, *, counter=None, timeout_delay: float = 3.2):
    if role not in {"primary", "secondary"}:
        raise ValueError("Unknown provider role")
    counter = counter or TokenCounter()

    async def complete(request):
        payload = await request.json()
        request.app.state.calls.append(payload)
        prompt = payload["prompt"]
        if "[both-fail]" in prompt:
            return JSONResponse(
                {"error": "private mock internals"},
                status_code=429 if role == "primary" else 500,
            )
        if "[error]" in prompt and role == "primary":
            return JSONResponse({"error": "private mock internals"}, status_code=500)
        if role == "primary" and "[429]" in prompt:
            return JSONResponse({"error": "private rate limit detail"}, status_code=429)
        if role == "primary" and "[timeout]" in prompt:
            await asyncio.sleep(timeout_delay)
        # A tiny text with real tokenizer-derived usage; cap it for small output budgets.
        text = "OK" if payload["max_tokens"] >= 1 else ""
        prompt_tokens = counter.count(prompt)
        completion_tokens = counter.count(text)
        return JSONResponse(
            {
                "choices": [{"text": text}],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        )

    app = Starlette(routes=[Route("/v1/completions", complete, methods=["POST"])])
    app.state.calls = []
    return app
