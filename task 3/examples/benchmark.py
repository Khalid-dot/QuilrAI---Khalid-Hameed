"""Measure bounded redactor memory without retaining input/output for the whole response."""

import json
import tracemalloc
from time import perf_counter

from llm_guardrail.redactor import StreamingRedactor


def main():
    chunk = "Safe text. Email x@example.com; Card 4111111111111111! " * 40
    repetitions = 500
    redactor = StreamingRedactor()
    tracemalloc.start()
    started = perf_counter()
    for _ in range(repetitions):
        safe = redactor.feed(chunk)
        assert "x@example.com" not in safe and "4111111111111111" not in safe
    redactor.finish()
    elapsed = perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert redactor.peak_pending_chars <= 448
    assert peak < 300_000
    print(
        json.dumps(
            {
                "input_characters": len(chunk) * repetitions,
                "peak_pending_characters": redactor.peak_pending_chars,
                "pending_character_bound": 448,
                "peak_traced_python_bytes": peak,
                "elapsed_seconds_with_tracing": round(elapsed, 3),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
