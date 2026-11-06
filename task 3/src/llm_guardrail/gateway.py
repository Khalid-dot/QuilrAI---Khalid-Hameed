"""Streaming text gateway. Raw provider payloads never pass directly to the client."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import anyio
import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from llm_guardrail.config import Settings
from llm_guardrail.redactor import StreamingRedactor
from llm_guardrail.sse import ProviderProtocolError, content_delta, event, sse_data

logger = logging.getLogger(__name__)


def failure(code: str, message: str, status: int):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


async def guarded_events(upstream: httpx.Response, settings: Settings):
    redactor = StreamingRedactor()
    try:
        async for data in sse_data(
            upstream.aiter_bytes(), max_event_bytes=settings.max_event_bytes
        ):
            if data == "[DONE]":
                tail = redactor.finish()
                if tail:
                    yield event("delta", {"text": tail})
                yield event("done", {"status": "complete"})
                return
            safe = redactor.feed(content_delta(data))
            if safe:
                yield event("delta", {"text": safe})
        raise ProviderProtocolError("Provider ended without completion marker")
    except (ProviderProtocolError, UnicodeError, httpx.RequestError):
        logger.warning("Provider stream failed; unresolved text discarded")
        yield event(
            "error",
            {
                "code": "provider_stream_error",
                "message": "Provider stream could not be completed",
            },
        )
    finally:
        # Starlette cancels the iterator on disconnect. Shield resource cleanup only.
        with anyio.CancelScope(shield=True):
            await upstream.aclose()


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

    async def complete(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            return failure("invalid_request", "Content-Type must be application/json", 415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > settings.max_request_bytes:
                return failure("invalid_request", "Request too large", 413)
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            return failure("invalid_request", "Invalid JSON", 400)
        if (
            not isinstance(payload, dict)
            or set(payload) - {"prompt", "max_tokens"}
            or not isinstance(payload.get("prompt"), str)
            or not 1 <= len(payload["prompt"]) <= 32_000
            or type(payload.get("max_tokens", 256)) is not int
            or not 1 <= payload.get("max_tokens", 256) <= 4096
        ):
            return failure("invalid_request", "Invalid completion parameters", 400)
        headers = {"Accept": "text/event-stream"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        provider_request = request.app.state.client.build_request(
            "POST",
            settings.provider_url,
            headers=headers,
            json={
                "model": settings.model,
                "messages": [{"role": "user", "content": payload["prompt"]}],
                "max_tokens": payload.get("max_tokens", 256),
                "stream": True,
                "n": 1,
            },
        )
        try:
            async with asyncio.timeout(settings.timeout_seconds):
                upstream = await request.app.state.client.send(provider_request, stream=True)
        except (httpx.TimeoutException, TimeoutError):
            return failure("provider_timeout", "Provider timed out", 504)
        except httpx.RequestError:
            return failure("provider_unavailable", "Provider unavailable", 502)
        if (
            upstream.status_code != 200
            or upstream.headers.get("content-type", "").split(";", 1)[0].lower()
            != "text/event-stream"
        ):
            await upstream.aclose()
            return failure("provider_unavailable", "Provider unavailable", 502)
        return StreamingResponse(
            guarded_events(upstream, settings),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
            background=BackgroundTask(upstream.aclose),
        )

    return Starlette(
        routes=[Route("/v1/completions", complete, methods=["POST"])],
        lifespan=lifespan,
    )
