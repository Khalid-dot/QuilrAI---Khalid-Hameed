"""Explicit encoding per endpoint; completion prompts have no chat-template overhead."""

import os
from pathlib import Path

import tiktoken

os.environ.setdefault(
    "TIKTOKEN_CACHE_DIR",
    str(Path(__file__).resolve().parents[2] / ".cache" / "tiktoken"),
)


class TokenCounter:
    def __init__(self, encoding: str = "cl100k_base"):
        self.encoding = tiktoken.get_encoding(encoding)

    def count(self, text: str) -> int:
        # Treat special-token-looking user input as ordinary text.
        return len(self.encoding.encode_ordinary(text))
