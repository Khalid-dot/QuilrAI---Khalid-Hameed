"""Official MCP SDK server with strict protocol validation errors."""

import asyncio
import json
import logging
import sys

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from pydantic import ValidationError

from quilrai_mcp.models import CustomerInput, RefundInput
from quilrai_mcp.service import CustomerNotFound, get_customer_record, trigger_refund

logger = logging.getLogger(__name__)
app = Server("quilrai-customer-mcp", version="0.1.0")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_customer_record",
            description="Read a mock customer record by CUST- followed by five digits.",
            inputSchema=CustomerInput.model_json_schema(),
        ),
        types.Tool(
            name="trigger_refund",
            description="Simulate a refund for a mock customer. No real payment is made.",
            inputSchema=RefundInput.model_json_schema(),
        ),
    ]


async def call_tool(request: types.CallToolRequest) -> types.ServerResult:
    name = request.params.name
    arguments = request.params.arguments if request.params.arguments is not None else {}
    try:
        if name == "get_customer_record":
            result = get_customer_record(CustomerInput.model_validate(arguments))
        elif name == "trigger_refund":
            result = trigger_refund(RefundInput.model_validate(arguments))
        else:
            raise McpError(types.ErrorData(code=types.INVALID_PARAMS, message="Unknown tool"))
    except ValidationError as exc:
        # Do not echo user-supplied values, URLs, or exception context into errors/logs.
        errors = [
            {"field": ".".join(map(str, e["loc"])), "type": e["type"], "message": e["msg"]}
            for e in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
        logger.warning("Tool argument validation rejected")
        raise McpError(
            types.ErrorData(code=types.INVALID_PARAMS, message="Invalid params", data=errors)
        ) from None
    except CustomerNotFound:
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text="Customer record not found")],
                isError=True,
            )
        )
    except McpError:
        raise
    except Exception:
        logger.error("Unexpected tool execution failure")
        raise McpError(
            types.ErrorData(code=types.INTERNAL_ERROR, message="Internal error")
        ) from None

    return types.ServerResult(
        types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, allow_nan=False))],
            structuredContent=result,
            isError=False,
        )
    )


# SDK 1.x's call_tool decorator catches exceptions and emits isError tool results.
# Register at its low-level request interface so McpError reaches the SDK's JSON-RPC
# dispatcher and becomes a -32602 protocol error.
app.request_handlers[types.CallToolRequest] = call_tool


async def run() -> None:
    logger.info("Starting customer MCP server over stdio")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Server stopped")


if __name__ == "__main__":
    main()
