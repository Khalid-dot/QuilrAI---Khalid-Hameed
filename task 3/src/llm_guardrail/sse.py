"""Bounded SSE framing over arbitrary network byte chunks, including split UTF-8."""

import json
from collections.abc import AsyncIterable, AsyncIterator


class ProviderProtocolError(ValueError):
    pass


def event(kind: str, payload: dict) -> bytes:
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n".encode()


async def sse_data(
    chunks: AsyncIterable[bytes],
    *,
    max_event_bytes: int = 65_536,
) -> AsyncIterator[str]:
    line = bytearray()
    data = []
    event_size = 0
    skip_lf = False
    first_line = True
    async for chunk in chunks:
        for byte in chunk:
            if skip_lf:
                skip_lf = False
                if byte == 10:
                    continue
            if byte not in (10, 13):
                line.append(byte)
                event_size += 1
                if event_size > max_event_bytes:
                    raise ProviderProtocolError("Provider event exceeds limit")
                continue
            skip_lf = byte == 13
            value = bytes(line)
            line.clear()
            if first_line:
                value = value.removeprefix(b"\xef\xbb\xbf")
                first_line = False
            if not value:
                if data:
                    yield b"\n".join(data).decode("utf-8", errors="strict")
                data.clear()
                event_size = 0
            elif value.startswith(b"data:"):
                data.append(value[5:].removeprefix(b" "))
                # Include line delimiters so repeated empty data lines stay bounded.
                event_size += 1
                if event_size > max_event_bytes:
                    raise ProviderProtocolError("Provider event exceeds limit")
            elif value == b"data":
                data.append(b"")
                event_size += 1
                if event_size > max_event_bytes:
                    raise ProviderProtocolError("Provider event exceeds limit")
    if line or data:
        raise ProviderProtocolError("Truncated provider event")


def content_delta(data: str) -> str:
    try:
        payload = json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise ProviderProtocolError("Invalid provider JSON") from exc
    if not isinstance(payload, dict) or "error" in payload:
        raise ProviderProtocolError("Invalid provider payload")
    choices = payload.get("choices")
    if choices == [] and "usage" in payload:
        return ""
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProviderProtocolError("Expected a single provider choice")
    choice = choices[0]
    if choice.get("index", 0) != 0 or not isinstance(choice.get("delta"), dict):
        raise ProviderProtocolError("Invalid provider delta")
    delta = choice["delta"]
    if any(key not in {"role", "content"} for key in delta):
        raise ProviderProtocolError("Only text deltas are supported")
    text = delta.get("content")
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ProviderProtocolError("Non-text provider content")
    return text
