import argparse
import logging

import uvicorn

from mcp_gateway.config import Settings
from mcp_gateway.gateway import create_app
from mcp_gateway.mock_server import create_mock_app


def gateway_main():
    parser = argparse.ArgumentParser(description="Role-aware MCP HTTP gateway")
    parser.add_argument("--demo", action="store_true", help="Enable documented local demo tokens")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        settings = Settings.from_env(demo=args.demo)
    except ValueError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)


def mock_main():
    parser = argparse.ArgumentParser(description="Local mock downstream MCP server")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run(create_mock_app(), host="127.0.0.1", port=args.port)
