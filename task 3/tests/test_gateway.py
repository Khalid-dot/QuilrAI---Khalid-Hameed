import asyncio
import json

import httpx
import pytest
from conftest import ProviderStream, gateway_client, streaming_response

from llm_guardrail.config import Settings
from llm_guardrail.gateway import guarded_events
from llm_guardrail.mock_provider import SAMPLE, provider_event
from llm_guardrail.sse import sse_data


async def parse_response(response):
    async def body():
        yield response.content

    return [json.loads(data) async for data in sse_data(body())]


async def test_endpoint_redacts_all_pii_from_one_character_provider_deltas():
    wire = b"".join(provider_event(char) for char in SAMPLE) + b"data: [DONE]\n\n"
    stream = ProviderStream([wire[i : i + 7] for i in range(0, len(wire), 7)])
    captured = []

    def provider(request):
        captured.append(request)
        return streaming_response(stream)

    async with gateway_client(provider, api_key="provider-secret") as client:
        response = await client.post("/v1/completions", json={"prompt": "Tell me something"})
    assert response.status_code == 200
    assert response.headers["x-accel-buffering"] == "no"
    events = await parse_response(response)
    text = "".join(item.get("text", "") for item in events)
    assert text == SAMPLE.replace("alex.morgan@example.com", "[REDACTED]").replace(
        "123-45-6789",
        "[REDACTED]",
    ).replace("4111 1111 1111 1111", "[REDACTED]")
    assert events[-1] == {"status": "complete"}
    assert len([item for item in events if "text" in item]) > 3
    assert stream.closed
    assert captured[0].headers["authorization"] == "Bearer provider-secret"
    payload = json.loads(captured[0].content)
    assert payload["stream"] is True
    assert payload["messages"] == [{"role": "user", "content": "Tell me something"}]
    assert "provider-secret" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        None,
        {"prompt": ""},
        {"prompt": 1},
        {"prompt": "test", "max_tokens": True},
        {"prompt": "test", "max_tokens": 0},
        {"prompt": "test", "max_tokens": 4097},
        {"prompt": "test", "provider_url": "http://evil"},
        {"prompt": "x" * 32_001},
    ],
)
async def test_invalid_requests_do_not_reach_provider(payload):
    reached = []
    async with gateway_client(lambda request: reached.append(request)) as client:
        response = await client.post(
            "/v1/completions",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 400
    assert reached == []


async def test_bad_json_content_type_and_body_limit():
    reached = []
    async with gateway_client(
        lambda request: reached.append(request), max_request_bytes=32
    ) as client:
        assert (await client.post("/v1/completions", content="{}")).status_code == 415
        assert (
            await client.post(
                "/v1/completions", content="{", headers={"Content-Type": "application/json"}
            )
        ).status_code == 400
        assert (await client.post("/v1/completions", json={"prompt": "x" * 100})).status_code == 413
    assert reached == []


@pytest.mark.parametrize("kind", ["connect", "timeout", "deadline", "status", "content-type"])
async def test_pre_stream_failures_are_sanitized(kind):
    async def provider(request):
        if kind == "connect":
            raise httpx.ConnectError("private URL and API key", request=request)
        if kind == "timeout":
            raise httpx.ReadTimeout("private URL and API key", request=request)
        if kind == "deadline":
            await asyncio.sleep(1)
        return httpx.Response(500 if kind == "status" else 200, text="private upstream failure")

    async with gateway_client(provider, timeout_seconds=0.02) as client:
        response = await client.post("/v1/completions", json={"prompt": "test"})
    assert response.status_code == (504 if kind in {"timeout", "deadline"} else 502)
    assert "private" not in response.text
    assert "error" in response.json()


@pytest.mark.parametrize(
    "ending",
    [
        b"",
        b"data: not-json\n\n",
        b'data: {"error":"private"}\n\n',
        b"data: " + b"x" * 100_000,
        b"data: unfinished",
    ],
)
async def test_stream_failure_discards_pending_text_and_emits_error(ending):
    stream = ProviderStream(
        [provider_event("Safe words. "), provider_event("secret@example"), ending]
    )
    async with gateway_client(lambda request: streaming_response(stream)) as client:
        response = await client.post("/v1/completions", json={"prompt": "test"})
    events = await parse_response(response)
    assert response.status_code == 200
    assert events[0] == {"text": "Safe words. "}
    assert events[-1]["code"] == "provider_stream_error"
    assert "secret" not in response.text
    assert "private" not in response.text
    assert "event: done" not in response.text
    assert stream.closed


async def test_midstream_timeout_closes_provider_and_drops_candidate():
    stream = ProviderStream([provider_event("secret@example")], error=httpx.ReadTimeout("private"))
    async with gateway_client(lambda request: streaming_response(stream)) as client:
        response = await client.post("/v1/completions", json={"prompt": "test"})
    assert stream.closed
    assert "secret" not in response.text
    assert "provider_stream_error" in response.text


async def test_backpressure_emits_before_reading_rest_and_closes_on_disconnect():
    stream = ProviderStream(
        [
            provider_event("Hello there. "),
            provider_event("secret@example.com "),
            b"data: [DONE]\n\n",
        ]
    )
    response = streaming_response(stream)
    iterator = guarded_events(response, Settings())
    first = await anext(iterator)
    assert b"Hello there." in first
    assert stream.read_count == 1
    assert not stream.closed
    await iterator.aclose()  # Client stops consuming before the next provider delta.
    assert stream.closed
    assert stream.read_count == 1


async def test_cancellation_closes_provider():
    entered = asyncio.Event()

    class WaitingStream(ProviderStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""

    stream = WaitingStream([])
    iterator = guarded_events(streaming_response(stream), Settings())
    task = asyncio.create_task(anext(iterator))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


async def test_concurrent_streams_have_independent_redactor_state():
    streams = []

    def provider(request):
        prompt = json.loads(request.content)["messages"][0]["content"]
        text = "Email first@example.com!" if prompt == "first" else "Card 4111111111111111!"
        stream = ProviderStream([*(provider_event(char) for char in text), b"data: [DONE]\n\n"])
        streams.append(stream)
        return streaming_response(stream)

    async with gateway_client(provider) as client:
        responses = await asyncio.gather(
            *[
                client.post("/v1/completions", json={"prompt": prompt})
                for prompt in ("first", "second")
            ]
        )
    texts = [
        "".join(item.get("text", "") for item in await parse_response(response))
        for response in responses
    ]
    assert texts == ["Email [REDACTED]!", "Card [REDACTED]!"]
    assert all(stream.closed for stream in streams)


async def test_preflight_failure_closes_provider_without_reading_private_body():
    stream = ProviderStream([b"private upstream stack trace"])
    async with gateway_client(
        lambda request: httpx.Response(500, stream=stream),
    ) as client:
        response = await client.post("/v1/completions", json={"prompt": "test"})
    assert response.status_code == 502
    assert stream.closed and stream.read_count == 0
    assert "private" not in response.text
