"""Authenticated non-streaming completion endpoint over the reusable routing module."""

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from uuid import uuid4

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from model_router.config import Settings
from model_router.errors import GatewayError
from model_router.limiter import SQLiteTokenLimiter
from model_router.router import ModelRouter

logger = logging.getLogger(__name__)


def authenticate(request: Request, keys: tuple[str, ...]) -> str | None:
    supplied = request.headers.getlist("x-api-key")
    if len(supplied) != 1 or not supplied[0].isascii():
        return None
    for key in keys:
        if hmac.compare_digest(supplied[0], key):
            return hashlib.sha256(key.encode()).hexdigest()
    return None


def create_app(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    counters: tuple | None = None,
):
    @asynccontextmanager
    async def lifespan(app):
        limiter = SQLiteTokenLimiter(
            settings.database,
            limit=settings.tokens_per_window,
            window_seconds=settings.window_seconds,
        )
        await limiter.initialize()
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            app.state.limiter = limiter
            app.state.router = ModelRouter(settings, limiter, client, counters=counters)
            yield

    async def complete(request: Request):
        request_id = uuid4().hex
        try:
            tenant = authenticate(request, settings.api_keys)
            if tenant is None:
                raise GatewayError("unauthorized", "Valid tenant API key required", 401)
            if (
                request.headers.get("content-type", "").split(";", 1)[0].lower()
                != "application/json"
            ):
                raise GatewayError("invalid_request", "Content-Type must be application/json", 415)
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 65_536:
                    raise GatewayError("invalid_request", "Request too large", 413)
                body.extend(chunk)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                raise GatewayError("invalid_request", "Invalid JSON", 400) from None
            if (
                not isinstance(payload, dict)
                or set(payload) - {"prompt", "max_tokens"}
                or not isinstance(payload.get("prompt"), str)
                or not 1 <= len(payload["prompt"]) <= 32_000
                or type(payload.get("max_tokens", 256)) is not int
                or not 1 <= payload.get("max_tokens", 256) <= 50_000
            ):
                raise GatewayError("invalid_request", "Invalid completion parameters", 400)
            result = await request.app.state.router.complete(
                tenant,
                payload["prompt"],
                payload.get("max_tokens", 256),
            )
            return JSONResponse({"request_id": request_id, **asdict(result)})
        except GatewayError as exc:
            headers = {"X-Request-Id": request_id}
            if exc.retry_after is not None:
                headers["Retry-After"] = str(exc.retry_after)
            return JSONResponse(
                {"error": {"code": exc.code, "message": exc.message, "request_id": request_id}},
                status_code=exc.status,
                headers=headers,
            )
        except Exception:
            logger.error("Unexpected gateway failure; request_id=%s", request_id)
            return JSONResponse(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "Internal gateway error",
                        "request_id": request_id,
                    }
                },
                status_code=500,
                headers={"X-Request-Id": request_id},
            )

    return Starlette(
        routes=[Route("/v1/completions", complete, methods=["POST"])], lifespan=lifespan
    )
