from contextlib import asynccontextmanager
from dataclasses import replace

import httpx

from model_router.config import DEMO_KEYS, Settings
from model_router.gateway import create_app


class CharacterCounter:
    """Deterministic test tokenizer; real-provider/demo tests use tiktoken."""

    def count(self, text):
        return len(text)


def success(*, prompt_tokens=2, completion_tokens=1):
    return httpx.Response(
        200,
        json={
            "choices": [{"text": "OK"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    )


@asynccontextmanager
async def gateway_client(tmp_path, handler, **overrides):
    settings = replace(
        Settings(api_keys=DEMO_KEYS, database=tmp_path / "router.sqlite3"), **overrides
    )
    app = create_app(
        settings,
        transport=httpx.MockTransport(handler),
        counters=(CharacterCounter(), CharacterCounter()),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway",
        ) as client:
            yield client, app


def auth(key=DEMO_KEYS[0]):
    return {"X-API-Key": key}
