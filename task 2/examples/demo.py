"""One-command real HTTP demo. Starts and stops both servers on free loopback ports."""

import asyncio
import socket
from contextlib import ExitStack

import httpx
import uvicorn

from mcp_gateway.config import DEMO_TOKENS, Settings
from mcp_gateway.gateway import create_app
from mcp_gateway.mock_server import create_mock_app


def listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


async def demonstrate(url, downstream):
    async with httpx.AsyncClient(trust_env=False) as client:

        async def call(request_id, role, method, params=None):
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                payload["params"] = params
            return await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer demo-{role}-token"},
            )

        response = await call(1, "viewer", "tools/list")
        assert response.status_code == 200
        print("Viewer discovers:", [tool["name"] for tool in response.json()["result"]["tools"]])
        response = await call(2, "viewer", "tools/call", {"name": "get_status", "arguments": {}})
        assert response.status_code == 200
        print("Viewer ordinary call:", response.json()["result"])

        before = len(downstream.state.received)
        response = await call(
            3,
            "viewer",
            "tools/call",
            {"name": "admin_reset_key", "arguments": {}},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == -32001
        assert len(downstream.state.received) == before
        print("Viewer admin call:", response.json())
        print("Denied call reached downstream: False")

        response = await call(
            4,
            "admin",
            "tools/call",
            {"name": "admin_reset_key", "arguments": {}},
        )
        assert response.status_code == 200
        assert len(downstream.state.received) == before + 1
        print("Admin call:", response.json()["result"])
        print("Demo passed: real HTTP forwarding and authorization verified.")


async def main():
    with ExitStack() as stack:
        downstream_socket = stack.enter_context(listener())
        gateway_socket = stack.enter_context(listener())
        downstream = create_mock_app()
        downstream_url = f"http://127.0.0.1:{downstream_socket.getsockname()[1]}/mcp"
        gateway = create_app(Settings(tokens=dict(DEMO_TOKENS), downstream_url=downstream_url))
        servers = [
            uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
            for app in (downstream, gateway)
        ]
        tasks = [
            asyncio.create_task(server.serve(sockets=[sock]))
            for server, sock in zip(servers, (downstream_socket, gateway_socket), strict=True)
        ]
        try:
            async with asyncio.timeout(10):
                while not all(server.started for server in servers):
                    for task in tasks:
                        if task.done():
                            task.result()
                            raise RuntimeError("Demo server exited before startup")
                    await asyncio.sleep(0.01)
            url = f"http://127.0.0.1:{gateway_socket.getsockname()[1]}/mcp"
            await demonstrate(url, downstream)
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
