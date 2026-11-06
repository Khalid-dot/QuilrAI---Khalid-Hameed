import asyncio

import httpx
import pytest
from conftest import bearer, gateway_client, tool_call


@pytest.mark.parametrize("kind", ["connect", "timeout", "deadline"])
async def test_upstream_failure_sanitized(kind):
    async def downstream(request):
        if kind == "connect":
            raise httpx.ConnectError("secret internal hostname", request=request)
        if kind == "timeout":
            raise httpx.ReadTimeout("secret upstream URL", request=request)
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    async with gateway_client(
        httpx.MockTransport(downstream),
        timeout_seconds=0.02,
    ) as client:
        response = await client.post("/mcp", json=tool_call(), headers=bearer())
    assert response.status_code == (502 if kind == "connect" else 504)
    assert response.json()["id"] == 7
    assert "secret" not in response.text
    assert "traceback" not in response.text.lower()


@pytest.mark.parametrize("status", [301, 400, 401, 429, 500])
async def test_http_failure_and_redirects_not_exposed(status):
    def downstream(request):
        return httpx.Response(status, text="private stack trace", headers={"Location": "/secret"})

    async with gateway_client(httpx.MockTransport(downstream)) as client:
        response = await client.post("/mcp", json=tool_call(), headers=bearer())
    assert response.status_code == 502
    assert "private" not in response.text
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"jsonrpc": "2.0", "id": 8, "result": {}},
        {"jsonrpc": "2.0", "id": 7, "result": {}, "error": {}},
        {"jsonrpc": "2.0", "id": 7, "error": {"code": True, "message": "invalid"}},
    ],
)
async def test_invalid_downstream_envelopes(payload):
    async with gateway_client(
        httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    ) as client:
        response = await client.post("/mcp", json=tool_call(), headers=bearer())
    assert response.status_code == 502
    assert response.json()["error"]["message"] == "Invalid downstream response"


async def test_downstream_non_json_response():
    async with gateway_client(
        httpx.MockTransport(lambda request: httpx.Response(200, text="private HTML failure")),
    ) as client:
        response = await client.post("/mcp", json=tool_call(), headers=bearer())
    assert response.status_code == 502
    assert "private" not in response.text
