from contextlib import asynccontextmanager

import httpx

from llm_guardrail.config import Settings
from llm_guardrail.gateway import create_app


class ProviderStream(httpx.AsyncByteStream):
    def __init__(self, parts, *, error=None):
        self.parts = parts
        self.error = error
        self.closed = False
        self.read_count = 0

    async def __aiter__(self):
        for part in self.parts:
            self.read_count += 1
            yield part
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


@asynccontextmanager
async def gateway_client(handler, **settings):
    app = create_app(Settings(**settings), transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway",
        ) as client:
            yield client


def streaming_response(stream):
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)
