import random
import tracemalloc

import pytest

from llm_guardrail.redactor import REDACTED, StreamingRedactor

CASES = [
    ("Email: alex.morgan@example.com!", "Email: [REDACTED]!"),
    ("x+y@sub.example.co.uk", REDACTED),
    ("SSN: 123-45-6789.", "SSN: [REDACTED]."),
    ("Card 4111111111111111.", "Card [REDACTED]."),
    ("Card 4111 1111 1111 1111!", "Card [REDACTED]!"),
    ("Card 4111-1111-1111-1111!", "Card [REDACTED]!"),
    ("a@b.com 123-45-6789 4111111111111111", "[REDACTED] [REDACTED] [REDACTED]"),
    ("Hello, world! Order 12345 is ready. Café ✅", "Hello, world! Order 12345 is ready. Café ✅"),
    ("No private information here.", "No private information here."),
    ("", ""),
]


def redact(chunks):
    redactor = StreamingRedactor()
    pieces = [redactor.feed(chunk) for chunk in chunks]
    pieces.append(redactor.finish())
    assert redactor.pending_chars == 0
    assert redactor.peak_pending_chars <= redactor.EMAIL_LIMIT + redactor.NUMBER_LIMIT
    return "".join(pieces)


@pytest.mark.parametrize("raw, expected", CASES)
def test_every_possible_two_chunk_split(raw, expected):
    for boundary in range(len(raw) + 1):
        assert redact([raw[:boundary], raw[boundary:]]) == expected


@pytest.mark.parametrize("raw, expected", CASES)
def test_single_character_chunks(raw, expected):
    assert redact(raw) == expected


@pytest.mark.parametrize("seed", range(30))
def test_random_chunk_partitions(seed):
    raw = "Prefix " + " | ".join(raw for raw, _ in CASES) + " end."
    expected = "Prefix " + " | ".join(expected for _, expected in CASES) + " end."
    rng = random.Random(seed)
    chunks = []
    position = 0
    while position < len(raw):
        length = rng.randint(1, 25)
        chunks.append(raw[position : position + length])
        position += length
    assert redact(chunks) == expected


def test_no_email_prefix_is_emitted_before_its_boundary():
    redactor = StreamingRedactor()
    assert redactor.feed("Hello ") == "Hello "
    for char in "alex@example.com":
        assert redactor.feed(char) == ""
    assert redactor.feed(" ") == "[REDACTED] "


def test_no_numeric_prefix_is_emitted_before_its_boundary():
    redactor = StreamingRedactor()
    assert redactor.feed("Card: ") == "Card: "
    for char in "4111 1111 1111 1111":
        assert redactor.feed(char) == ""
    assert redactor.feed("! ") == "[REDACTED]! "


def test_oversized_email_atom_fails_closed_then_recovers():
    raw = "a" * 10_000 + "@example.com next."
    assert redact([raw[:500], raw[500:]]) == "[REDACTED] next."


def test_oversized_numeric_run_fails_closed_then_recovers():
    raw = "1 " * 10_000 + "next."
    result = redact([raw[index : index + 17] for index in range(0, len(raw), 17)])
    assert result == "[REDACTED]next."


def test_normal_text_emits_before_stream_completion():
    redactor = StreamingRedactor()
    assert redactor.feed("Hello there, ") == "Hello there, "
    assert redactor.feed("more text ") == "more text "


def test_stream_memory_does_not_grow_with_response_length():
    redactor = StreamingRedactor()
    chunk = "Safe text. Email x@example.com; Card 4111111111111111! " * 40
    tracemalloc.start()
    try:
        for _ in range(500):
            result = redactor.feed(chunk)
            assert "x@example.com" not in result
            assert "4111111111111111" not in result
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 300_000
    assert redactor.peak_pending_chars <= 448


def test_finished_redactor_cannot_be_reused():
    redactor = StreamingRedactor()
    redactor.finish()
    with pytest.raises(RuntimeError):
        redactor.feed("more")
    with pytest.raises(RuntimeError):
        redactor.finish()
