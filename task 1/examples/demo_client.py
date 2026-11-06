"""Run from task 1: .venv/bin/python examples/demo_client.py."""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError


async def main() -> None:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "quilrai_mcp"])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Tools:", [tool.name for tool in (await session.list_tools()).tools])
            for name, arguments in [
                ("get_customer_record", {"customer_id": "CUST-00001"}),
                (
                    "trigger_refund",
                    {"customer_id": "CUST-00001", "amount": 25.5, "reason": "Duplicate payment"},
                ),
            ]:
                result = await session.call_tool(name, arguments)
                print(name, json.dumps(result.structuredContent, indent=2))
            try:
                await session.call_tool("get_customer_record", {"customer_id": "bad-id"})
            except McpError as exc:
                print("Expected validation error:", exc.error.model_dump(exclude_none=True))
            result = await session.call_tool("get_customer_record", {"customer_id": "CUST-99999"})
            print("Unknown customer:", result.model_dump(exclude_none=True))


if __name__ == "__main__":
    asyncio.run(main())
