"""Server-controlled opaque tokens: callers cannot choose or assert their own role."""

import json
import math
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

DEMO_TOKENS = {"demo-admin-token": "admin", "demo-viewer-token": "viewer"}


@dataclass(frozen=True)
class Settings:
    tokens: dict[str, str]
    downstream_url: str = "http://127.0.0.1:8001/mcp"
    timeout_seconds: float = 5.0
    max_body_bytes: int = 1_048_576

    def __post_init__(self):
        if (
            not isinstance(self.tokens, dict)
            or not self.tokens
            or any(
                not isinstance(token, str)
                or not token
                or not token.isascii()
                or any(char.isspace() for char in token)
                or not isinstance(role, str)
                or role not in {"admin", "viewer"}
                for token, role in self.tokens.items()
            )
        ):
            raise ValueError("Configure nonempty ASCII tokens with admin or viewer roles")
        url = urlsplit(self.downstream_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("Downstream must be an HTTP(S) URL without embedded credentials")
        if (
            not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.max_body_bytes <= 0
        ):
            raise ValueError("Timeout and body limit must be positive")

    @classmethod
    def from_env(cls, *, demo: bool = False):
        raw = os.environ.get("GATEWAY_TOKENS")
        if raw:
            try:
                tokens = json.loads(raw)
            except ValueError:
                raise ValueError("GATEWAY_TOKENS must be a JSON object") from None
            if not isinstance(tokens, dict):
                raise ValueError("GATEWAY_TOKENS must be a JSON object")
        elif demo:
            tokens = dict(DEMO_TOKENS)
        else:
            raise ValueError("Set GATEWAY_TOKENS or explicitly enable --demo")
        return cls(
            tokens=tokens,
            downstream_url=os.environ.get("MCP_DOWNSTREAM_URL", "http://127.0.0.1:8001/mcp"),
        )
