"""Asynchronous, JSON-only HTTP MCP reverse proxy with per-call authorization."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from mcp_gateway.auth import authenticate
from mcp_gateway.config import Settings
from mcp_gateway.protocol import (
    InvalidEnvelope,
    decode_json,
    rpc_error,
    valid_id,
    valid_response,
    validate_request,
)

logger = logging.getLogger(__name__)
REQUEST_HEADERS = {"accept", "mcp-session-id", "mcp-protocol-version", "last-event-id"}
RESPONSE_HEADERS = {"content-type", "mcp-session-id", "mcp-protocol-version"}


def create_app(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            transport=transport,
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            app.state.client = client
            yield

    async def proxy(request: Request):
        role = authenticate(request, settings.tokens)
        if role is None:
            return rpc_error(
                None,
                -32000,
                "Authentication required",
                401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            return rpc_error(None, -32600, "Content-Type must be application/json", 415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > settings.max_body_bytes:
                return rpc_error(None, -32600, "Request too large", 413)
        try:
            payload = decode_json(bytes(body))
        except (ValueError, UnicodeError, RecursionError):
            return rpc_error(None, -32700, "Parse error", 400)
        request_id = (
            payload.get("id") if isinstance(payload, dict) and valid_id(payload.get("id")) else None
        )
        try:
            validate_request(payload)
        except InvalidEnvelope:
            return rpc_error(request_id, -32600, "Invalid Request", 400)
        if payload["method"] == "tools/call":
            params = payload.get("params", {})
            name = params.get("name")
            if (
                not isinstance(name, str)
                or not name
                or ("arguments" in params and not isinstance(params["arguments"], dict))
            ):
                return rpc_error(request_id, -32602, "Invalid params", 400)
            if name.startswith("admin_") and role != "admin":
                logger.info("Blocked unauthorized administrative tool call")
                return rpc_error(request_id, -32001, "Unauthorized Tool Call", 403)

        # Never forward client credentials, Host, cookies, or hop-by-hop headers.
        headers = {key: value for key, value in request.headers.items() if key in REQUEST_HEADERS}
        headers["content-type"] = "application/json"
        try:
            async with asyncio.timeout(settings.timeout_seconds):
                upstream = await request.app.state.client.post(
                    settings.downstream_url,
                    content=bytes(body),
                    headers=headers,
                )
        except (httpx.TimeoutException, TimeoutError):
            logger.warning("Downstream request timed out")
            return rpc_error(request_id, -32003, "Downstream timeout", 504)
        except httpx.RequestError:
            logger.warning("Downstream connection failed")
            return rpc_error(request_id, -32002, "Downstream unavailable", 502)
        if not 200 <= upstream.status_code < 300:
            return rpc_error(request_id, -32002, "Downstream unavailable", 502)
        if "id" not in payload:
            # Notifications have no JSON-RPC response.
            return Response(status_code=202)
        try:
            result = decode_json(upstream.content)
            if not valid_response(result, request_id):
                raise ValueError("Invalid upstream envelope")
        except (ValueError, UnicodeError, RecursionError):
            return rpc_error(request_id, -32002, "Invalid downstream response", 502)
        response_headers = {
            key: value for key, value in upstream.headers.items() if key in RESPONSE_HEADERS
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
        )

    return Starlette(routes=[Route("/mcp", proxy, methods=["POST"])], lifespan=lifespan)
