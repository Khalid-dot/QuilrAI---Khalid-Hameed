import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEMO_KEYS = ("demo-tenant-a-key", "demo-tenant-b-key")


@dataclass(frozen=True)
class Provider:
    url: str
    model: str
    api_key: str | None = None
    encoding: str = "cl100k_base"

    def __post_init__(self):
        url = urlsplit(self.url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("Provider must be an HTTP(S) URL without embedded credentials")
        if not self.model or not self.encoding:
            raise ValueError("Provider model and encoding are required")


@dataclass(frozen=True)
class Settings:
    api_keys: tuple[str, ...]
    database: Path = Path("data/router.sqlite3")
    primary: Provider = Provider("http://127.0.0.1:8005/v1/completions", "mock-primary")
    secondary: Provider = Provider("http://127.0.0.1:8006/v1/completions", "mock-secondary")
    tokens_per_window: int = 50_000
    window_seconds: float = 60.0
    primary_timeout_seconds: float = 3.0
    secondary_timeout_seconds: float = 3.0

    def __post_init__(self):
        if not self.api_keys or any(
            not isinstance(key, str)
            or not key
            or not key.isascii()
            or any(char.isspace() for char in key)
            for key in self.api_keys
        ):
            raise ValueError("Configure nonempty ASCII tenant API keys")
        if str(self.database) == ":memory:":
            raise ValueError("An on-disk SQLite path is required")
        if type(self.tokens_per_window) is not int or self.tokens_per_window <= 0:
            raise ValueError("Token capacity must be a positive integer")
        for value in (
            self.window_seconds,
            self.primary_timeout_seconds,
            self.secondary_timeout_seconds,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Windows and deadlines must be positive and finite")

    @classmethod
    def from_env(cls, *, demo: bool = False, database: str | None = None):
        raw = os.environ.get("TENANT_API_KEYS")
        if raw is None and demo:
            keys = DEMO_KEYS
        else:
            try:
                keys = json.loads(raw or "null")
            except ValueError:
                raise ValueError("TENANT_API_KEYS must be a JSON list") from None
            if not isinstance(keys, list):
                raise ValueError("Set TENANT_API_KEYS to a JSON list or enable --demo")
        providers = []
        for name, port in (("PRIMARY", 8005), ("SECONDARY", 8006)):
            providers.append(
                Provider(
                    os.environ.get(f"{name}_URL", f"http://127.0.0.1:{port}/v1/completions"),
                    os.environ.get(f"{name}_MODEL", f"mock-{name.lower()}"),
                    os.environ.get(f"{name}_API_KEY") or None,
                    os.environ.get(f"{name}_ENCODING", "cl100k_base"),
                )
            )
        return cls(
            api_keys=tuple(keys),
            database=Path(
                database
                or os.environ.get(
                    "ROUTER_DATABASE",
                    "data/router.sqlite3",
                )
            ),
            primary=providers[0],
            secondary=providers[1],
        )
