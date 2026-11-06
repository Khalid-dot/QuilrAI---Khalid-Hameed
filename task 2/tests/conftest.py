from contextlib import asynccontextmanager

import httpx
import pytest

from mcp_gateway.config import DEMO_TOKENS, Settings
from mcp_gateway.gateway import create_app
from mcp_gateway.mock_server import create_mock_app


@asynccontextmanager
async def gateway_client(transport, **settings):
    app = create_app(Settings(tokens=dict(DEMO_TOKENS), **settings), transport=transport)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway",
        ) as client:
            yield client


@pytest.fixture
async def connected():
    downstream = create_mock_app()
    async with gateway_client(httpx.ASGITransport(app=downstream)) as client:
        yield client, downstream.state.received


def bearer(role="viewer"):
    return {"Authorization": f"Bearer demo-{role}-token"}


def tool_call(name="get_status", request_id=7):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }
