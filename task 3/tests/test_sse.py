import json

import pytest

from llm_guardrail.mock_provider import provider_event
from llm_guardrail.sse import ProviderProtocolError, content_delta, sse_data


async def chunks(parts):
    for part in parts:
        yield part


@pytest.mark.parametrize("newline", [b"\n", b"\r", b"\r\n"])
async def test_every_byte_boundary_and_sse_line_endings(newline):
    body = (
        b"\xef\xbb\xbf: keepalive"
        + newline
        + newline
        + b"event: message"
        + newline
        + b"data: caf\xc3\xa9"
        + newline
        + b"data: second line"
        + newline
        + newline
        + b"data: [DONE]"
        + newline
        + newline
    )
    for boundary in range(len(body) + 1):
        assert [item async for item in sse_data(chunks([body[:boundary], body[boundary:]]))] == [
            "café\nsecond line",
            "[DONE]",
        ]
    assert [item async for item in sse_data(chunks(bytes([b]) for b in body))] == [
        "café\nsecond line",
        "[DONE]",
    ]


@pytest.mark.parametrize("body", [b"data: unfinished", b"data: unfinished\n", b"data: \xff\n\n"])
async def test_truncated_and_invalid_utf8_events(body):
    with pytest.raises((ProviderProtocolError, UnicodeError)):
        _ = [item async for item in sse_data(chunks([body]))]


@pytest.mark.parametrize(
    "body", [b"data: " + b"x" * 100, b"data:\n" * 100, b":comment\n" * 100, b"data\n" * 100]
)
async def test_event_buffer_limits_apply_even_to_empty_data_and_comments(body):
    with pytest.raises(ProviderProtocolError, match="limit"):
        _ = [item async for item in sse_data(chunks([body]), max_event_bytes=32)]


async def test_multiple_events_in_one_network_chunk():
    result = [
        item async for item in sse_data(chunks([provider_event("Hi ") + provider_event("there")]))
    ]
    assert [content_delta(item) for item in result] == ["Hi ", "there"]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"error": {"message": "private"}},
        {"choices": []},
        {"choices": [{"delta": {"content": 123}}]},
        {"choices": [{"delta": {"tool_calls": [{"secret": "private"}]}}]},
        {"choices": [{"delta": {}}, {"delta": {}}]},
        {"choices": [{"index": 1, "delta": {"content": "private"}}]},
    ],
)
def test_invalid_provider_shapes(payload):
    with pytest.raises(ProviderProtocolError):
        content_delta(json.dumps(payload))


def test_metadata_is_not_forwarded():
    assert content_delta('{"choices":[{"delta":{"role":"assistant"}}]}') == ""
    assert content_delta('{"choices":[],"usage":{"total_tokens":10}}') == ""
    assert content_delta('{"choices":[{"delta":{"content":null}}]}') == ""
    with pytest.raises(ProviderProtocolError):
        content_delta("not-json")
