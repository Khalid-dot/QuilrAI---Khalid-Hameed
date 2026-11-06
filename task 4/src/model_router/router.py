"""Per-attempt token admission, strict total deadlines, and sequential fallback."""

import asyncio
import json
import logging
from dataclasses import dataclass

import httpx

from model_router.config import Provider, Settings
from model_router.errors import GatewayError
from model_router.limiter import SQLiteTokenLimiter
from model_router.tokens import TokenCounter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Completion:
    text: str
    usage: dict[str, int]
    provider: str
    model: str


class AttemptFailure(Exception):
    def __init__(self, kind: str):
        self.kind = kind


def parse_completion(body: bytes, max_tokens: int) -> tuple[str, dict[str, int]]:
    try:
        payload = json.loads(body)
        choices = payload["choices"]
        usage = payload["usage"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError
        text = choices[0]["text"]
        if not isinstance(text, str) or not isinstance(usage, dict):
            raise ValueError
        usage = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        if any(type(value) is not int or not 0 <= value <= 2**63 - 1 for value in usage.values()):
            raise ValueError
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            raise ValueError
        if usage["completion_tokens"] > max_tokens:
            raise ValueError
        return text, usage
    except (ValueError, KeyError, TypeError, IndexError, RecursionError):
        raise AttemptFailure("failure") from None


class ModelRouter:
    def __init__(
        self,
        settings: Settings,
        limiter: SQLiteTokenLimiter,
        client: httpx.AsyncClient,
        *,
        counters: tuple | None = None,
    ):
        self.settings = settings
        self.limiter = limiter
        self.client = client
        self.counters = counters or (
            TokenCounter(settings.primary.encoding),
            TokenCounter(settings.secondary.encoding),
        )

    async def _attempt(self, provider: Provider, prompt: str, max_tokens: int, deadline: float):
        headers = {"Accept": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        try:
            async with asyncio.timeout(deadline):
                async with self.client.stream(
                    "POST",
                    provider.url,
                    headers=headers,
                    json={
                        "model": provider.model,
                        "prompt": prompt,
                        "max_tokens": max_tokens,
                        "stream": False,
                    },
                    timeout=deadline,
                ) as response:
                    if response.status_code == 429:
                        raise AttemptFailure("rate_limit")
                    if response.status_code != 200:
                        raise AttemptFailure("failure")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 1_048_576:
                            raise AttemptFailure("failure")
                        body.extend(chunk)
                    return parse_completion(bytes(body), max_tokens)
        except (TimeoutError, httpx.TimeoutException):
            raise AttemptFailure("timeout") from None
        except httpx.RequestError:
            raise AttemptFailure("failure") from None

    async def complete(self, tenant: str, prompt: str, max_tokens: int) -> Completion:
        endpoints = (
            ("primary", self.settings.primary, self.settings.primary_timeout_seconds),
            ("secondary", self.settings.secondary, self.settings.secondary_timeout_seconds),
        )
        for index, (name, provider, deadline) in enumerate(endpoints):
            prompt_tokens = await asyncio.to_thread(self.counters[index].count, prompt)
            reservation = await self.limiter.reserve(tenant, prompt_tokens + max_tokens)
            try:
                text, usage = await self._attempt(provider, prompt, max_tokens, deadline)
            except AttemptFailure as exc:
                # 429 is an explicit rejection; other attempts may have consumed tokens.
                await self.limiter.settle(
                    reservation,
                    0 if exc.kind == "rate_limit" else reservation.tokens,
                )
                if index == 0 and exc.kind in {"rate_limit", "timeout"}:
                    logger.info("Primary unavailable; routing to secondary")
                    continue
                if exc.kind == "timeout":
                    raise GatewayError(
                        "provider_timeout", "Completion provider timed out", 504
                    ) from None
                raise GatewayError(
                    "provider_unavailable", "Completion provider unavailable", 502
                ) from None
            # Cancellation leaves a conservative persisted reservation, never a quota refund.
            await self.limiter.settle(reservation, usage["total_tokens"])
            return Completion(text=text, usage=usage, provider=name, model=provider.model)
        raise AssertionError("Unreachable routing state")
