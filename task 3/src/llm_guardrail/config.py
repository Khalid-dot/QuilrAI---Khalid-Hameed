import math
import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    provider_url: str = "http://127.0.0.1:8003/v1/chat/completions"
    model: str = "mock-model"
    api_key: str | None = None
    timeout_seconds: float = 10.0
    max_event_bytes: int = 65_536
    max_request_bytes: int = 65_536

    def __post_init__(self):
        url = urlsplit(self.provider_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("Provider must be an HTTP(S) URL without embedded credentials")
        if not self.model or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Model and positive timeout are required")
        if self.max_event_bytes <= 0 or self.max_request_bytes <= 0:
            raise ValueError("Buffer limits must be positive")

    @classmethod
    def from_env(cls):
        return cls(
            provider_url=os.environ.get(
                "LLM_PROVIDER_URL",
                "http://127.0.0.1:8003/v1/chat/completions",
            ),
            model=os.environ.get("LLM_MODEL", "mock-model"),
            api_key=os.environ.get("LLM_API_KEY") or None,
        )
