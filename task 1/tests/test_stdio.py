"""Exercise real child processes: wire errors, protocol purity, and SDK interoperability."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


@asynccontextmanager
async def wire_server():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "quilrai_mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=ENV,
    )
    try:
        yield process
    finally:
        process.stdin.close()
        await process.stdin.wait_closed()
        await asyncio.wait_for(process.wait(), timeout=10)
        assert process.returncode == 0
        trailing = await process.stdout.read()
        assert trailing == b"", f"Unexpected stdout: {trailing!r}"
        logs = (await process.stderr.read()).decode()
        assert "Starting customer MCP server over stdio" in logs
        assert "Traceback" not in logs


async def send(process, payload):
    process.stdin.write((json.dumps(payload) + "\n").encode())
    await process.stdin.drain()


async def request(process, request_id, method, params=None):
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    await send(process, payload)
    line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
    response = json.loads(line)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request_id
    assert ("result" in response) != ("error" in response)
    return response


async def initialize(process):
    result = await request(
        process,
        1,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "wire-test", "version": "1.0"},
        },
    )
    assert "tools" in result["result"]["capabilities"]
    await send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})


async def test_wire_protocol_validation_and_stdout_isolation():
    async with wire_server() as process:
        await initialize(process)
        tools = await request(process, "list", "tools/list")
        assert {tool["name"] for tool in tools["result"]["tools"]} == {
            "get_customer_record",
            "trigger_refund",
        }
        invalid = [
            ("get_customer_record", {}),
            ("get_customer_record", {"customer_id": "CUST-ABCDE"}),
            ("get_customer_record", {"customer_id": 12345}),
            ("get_customer_record", {"customer_id": "CUST-00001", "extra": 1}),
            (
                "trigger_refund",
                {"customer_id": "CUST-00001", "amount": -1, "reason": "Duplicate payment"},
            ),
            (
                "trigger_refund",
                {"customer_id": "CUST-00001", "amount": "10", "reason": "Duplicate payment"},
            ),
            (
                "trigger_refund",
                {"customer_id": "CUST-00001", "amount": True, "reason": "Duplicate payment"},
            ),
            ("trigger_refund", {"customer_id": "CUST-00001", "amount": 10, "reason": "Too short"}),
            ("unknown_tool", {}),
        ]
        for index, (name, arguments) in enumerate(invalid, start=10):
            response = await request(
                process,
                index,
                "tools/call",
                {
                    "name": name,
                    "arguments": arguments,
                },
            )
            assert response["error"]["code"] == -32602
        response = await request(
            process,
            "valid",
            "tools/call",
            {
                "name": "trigger_refund",
                "arguments": {
                    "customer_id": "CUST-00001",
                    "amount": 12.5,
                    "reason": "Duplicate payment",
                },
            },
        )
        assert response["result"]["structuredContent"]["status"] == "simulated"
        response = await request(
            process,
            "missing",
            "tools/call",
            {
                "name": "get_customer_record",
                "arguments": {"customer_id": "CUST-99999"},
            },
        )
        assert response["result"]["isError"] is True
        response = await request(process, "unknown-method", "unknown/method")
        # SDK 1.x rejects unknown method names while parsing its request union.
        assert response["error"]["code"] == -32602
        response = await request(process, "unsupported", "resources/list")
        assert response["error"]["code"] == -32601
        assert "result" in await request(process, "still-alive", "ping")


async def test_official_client_interoperability():
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "quilrai_mcp"],
        env=ENV,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            assert len((await session.list_tools()).tools) == 2
            result = await session.call_tool("get_customer_record", {"customer_id": "CUST-00001"})
            assert result.isError is False
            assert result.structuredContent["name"] == "Alex Morgan"
            with pytest.raises(McpError) as error:
                await session.call_tool("get_customer_record", {"customer_id": "bad"})
            assert error.value.error.code == -32602
