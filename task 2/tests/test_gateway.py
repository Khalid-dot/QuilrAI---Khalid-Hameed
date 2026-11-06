import asyncio
import json

import httpx
import pytest
from conftest import bearer, gateway_client, tool_call

from mcp_gateway.mock_server import TOOLS


@pytest.mark.parametrize("role", ["admin", "viewer"])
async def test_tools_list_is_transparent_and_includes_admin_tools(connected, role):
    client, received = connected
    payload = {"jsonrpc": "2.0", "id": "discover", "method": "tools/list", "params": {}}
    response = await client.post("/mcp", json=payload, headers=bearer(role))
    assert response.status_code == 200
    assert response.json() == {"jsonrpc": "2.0", "id": "discover", "result": {"tools": TOOLS}}
    assert received == [payload]


@pytest.mark.parametrize("role", ["admin", "viewer"])
async def test_ordinary_tools_allowed_for_both_roles(connected, role):
    client, received = connected
    payload = tool_call()
    response = await client.post("/mcp", json=payload, headers=bearer(role))
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert received == [payload]


async def test_admin_tool_allowed_for_admin(connected):
    client, received = connected
    payload = tool_call("admin_reset_key", "admin-call")
    response = await client.post("/mcp", json=payload, headers=bearer("admin"))
    assert response.status_code == 200
    assert "reset" in response.json()["result"]["content"][0]["text"]
    assert received == [payload]


@pytest.mark.parametrize("name", ["admin_reset_key", "admin_", "admin_any_future_tool"])
async def test_viewer_denied_before_any_downstream_call(connected, name):
    client, received = connected
    response = await client.post("/mcp", json=tool_call(name, "blocked"), headers=bearer())
    assert response.status_code == 403
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "blocked",
        "error": {"code": -32001, "message": "Unauthorized Tool Call"},
    }
    assert received == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic demo-admin-token"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer unknown-token"},
        {"Authorization": "Bearer viewer"},
        {"Authorization": "Bearer demo-admin-token extra"},
        [
            ("Authorization", "Bearer demo-viewer-token"),
            ("Authorization", "Bearer demo-admin-token"),
        ],
    ],
)
async def test_bad_auth_fails_closed(connected, headers):
    client, received = connected
    response = await client.post("/mcp", json=tool_call("admin_reset_key"), headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == -32000
    assert received == []


async def test_forged_role_does_not_elevate_viewer(connected):
    client, received = connected
    payload = tool_call("admin_reset_key")
    payload["role"] = "admin"
    response = await client.post(
        "/mcp",
        json=payload,
        headers={**bearer(), "X-Role": "admin"},
    )
    assert response.status_code == 403
    assert received == []


async def test_case_insensitive_bearer_scheme(connected):
    client, received = connected
    response = await client.post(
        "/mcp",
        json=tool_call(),
        headers={"Authorization": "bEaReR demo-viewer-token"},
    )
    assert response.status_code == 200
    assert len(received) == 1


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [tool_call("admin_reset_key")],
        None,
        1,
        "request",
        {},
        {"jsonrpc": "1.0", "method": "tools/list", "id": 1},
        {"jsonrpc": "2.0", "method": "tools/list", "id": True},
        {"jsonrpc": "2.0", "method": "tools/list", "id": []},
        {"jsonrpc": "2.0", "method": "tools/list", "id": 1.5},
        {"jsonrpc": "2.0", "method": 1, "id": 1},
        {"jsonrpc": "2.0", "method": "tools/call", "params": []},
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "admin_reset_key"}},
    ],
)
async def test_invalid_envelopes_never_reach_downstream(connected, payload):
    client, received = connected
    response = await client.post(
        "/mcp",
        content=json.dumps(payload),
        headers={**bearer(), "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600
    assert received == []


@pytest.mark.parametrize(
    "params", [{}, {"name": 1}, {"name": ""}, {"name": "get_status", "arguments": []}]
)
async def test_malformed_tool_params(connected, params):
    client, received = connected
    payload = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": params}
    response = await client.post("/mcp", json=payload, headers=bearer())
    assert response.json()["error"]["code"] == -32602
    assert response.json()["id"] == 3
    assert received == []


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"\xff",
        b'{"id": NaN}',
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
        b'{"name":"admin_reset_key","name":"get_status"}}',
    ],
)
async def test_bad_json_and_duplicate_names_rejected(connected, body):
    client, received = connected
    response = await client.post(
        "/mcp",
        content=body,
        headers={**bearer(), "Content-Type": "application/json"},
    )
    assert response.json()["error"]["code"] == -32700
    assert received == []


async def test_content_type_required(connected):
    client, received = connected
    response = await client.post("/mcp", content="{}", headers=bearer())
    assert response.status_code == 415
    assert received == []


async def test_request_size_limit():
    reached = []
    async with gateway_client(
        httpx.MockTransport(lambda request: reached.append(request)),
        max_body_bytes=10,
    ) as client:
        response = await client.post("/mcp", json=tool_call(), headers=bearer())
    assert response.status_code == 413
    assert reached == []


async def test_raw_bytes_and_safe_headers_forwarded_and_response_preserved():
    body = b'{ "jsonrpc": "2.0", "id": "raw", "method": "tools/list" }'
    result = b'{ "jsonrpc": "2.0", "id": "raw", "result": {"tools": []} }'
    reached = []

    def downstream(request):
        reached.append(request)
        assert request.content == body
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "x-role" not in request.headers
        assert request.headers["mcp-session-id"] == "session-1"
        assert request.headers["mcp-protocol-version"] == "2025-11-25"
        return httpx.Response(
            200,
            content=result,
            headers={
                "Content-Type": "application/json",
                "Mcp-Session-Id": "session-1",
                "Set-Cookie": "secret=bad",
                "X-Internal": "private",
            },
        )

    async with gateway_client(httpx.MockTransport(downstream)) as client:
        response = await client.post(
            "/mcp",
            content=body,
            headers={
                **bearer(),
                "Content-Type": "application/json",
                "Cookie": "secret=value",
                "Mcp-Session-Id": "session-1",
                "Mcp-Protocol-Version": "2025-11-25",
                "X-Role": "admin",
            },
        )
    assert len(reached) == 1
    assert response.content == result
    assert response.headers["mcp-session-id"] == "session-1"
    assert "set-cookie" not in response.headers
    assert "x-internal" not in response.headers


async def test_lifecycle_notifications_and_downstream_rpc_errors(connected):
    client, received = connected
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        },
        headers=bearer(),
    )
    assert response.json()["result"]["serverInfo"]["name"] == "quilrai-mock"
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        },
        headers=bearer(),
    )
    assert response.status_code == 202
    assert response.content == b""
    response = await client.post("/mcp", json=tool_call("missing"), headers=bearer())
    assert response.status_code == 200
    assert response.json()["error"] == {"code": -32602, "message": "Unknown tool"}
    assert len(received) == 3


async def test_concurrent_mixed_roles_keep_authorization_isolated(connected):
    client, received = connected
    responses = await asyncio.gather(
        *[
            client.post(
                "/mcp",
                json=tool_call("admin_reset_key", index),
                headers=bearer("admin" if index % 2 else "viewer"),
            )
            for index in range(20)
        ]
    )
    assert [r.status_code for r in responses] == [403 if i % 2 == 0 else 200 for i in range(20)]
    assert {payload["id"] for payload in received} == set(range(1, 20, 2))
