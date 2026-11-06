"""Deterministic downstream JSON-RPC mock. Bind to loopback; it has no authentication."""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mcp_gateway.protocol import decode_json, rpc_error, validate_request

TOOLS = [
    {
        "name": "get_status",
        "description": "Return mock system status",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "admin_reset_key",
        "description": "Simulate an administrative key reset",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def create_mock_app():
    async def handle(request: Request):
        try:
            payload = decode_json(await request.body())
            validate_request(payload)
        except (ValueError, UnicodeError, RecursionError):
            return rpc_error(None, -32600, "Invalid Request", 400)
        request.app.state.received.append(payload)
        if "id" not in payload:
            return Response(status_code=202)
        request_id = payload["id"]
        method = payload["method"]
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "quilrai-mock", "version": "0.1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = payload.get("params", {})
            name = params.get("name")
            if name not in {tool["name"] for tool in TOOLS}:
                return rpc_error(request_id, -32602, "Unknown tool", 200)
            if params.get("arguments", {}) != {}:
                return rpc_error(request_id, -32602, "Invalid params", 200)
            text = "Mock service healthy" if name == "get_status" else "Mock key reset simulated"
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            return rpc_error(request_id, -32601, "Method not found", 200)
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

    app = Starlette(routes=[Route("/mcp", handle, methods=["POST"])])
    app.state.received = []
    return app
