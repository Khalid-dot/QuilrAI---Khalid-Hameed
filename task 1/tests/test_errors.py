import pytest
from mcp import types
from mcp.shared.exceptions import McpError

from quilrai_mcp import server


async def test_unexpected_backend_failure_is_sanitized(monkeypatch):
    def broken_backend(arguments):
        raise RuntimeError("secret backend credentials and implementation details")

    monkeypatch.setattr(server, "get_customer_record", broken_backend)
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="get_customer_record", arguments={"customer_id": "CUST-00001"}
        ),
    )
    with pytest.raises(McpError) as error:
        await server.call_tool(request)
    assert error.value.error.code == -32603
    assert error.value.error.message == "Internal error"
    assert error.value.error.data is None
