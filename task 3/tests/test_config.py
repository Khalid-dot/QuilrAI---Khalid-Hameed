import pytest

from llm_guardrail.config import Settings


@pytest.mark.parametrize("url", ["file:///tmp/model", "http://", "http://secret:key@host/v1"])
def test_bad_provider_urls_rejected(url):
    with pytest.raises(ValueError):
        Settings(provider_url=url)


@pytest.mark.parametrize(
    "values",
    [
        {"model": ""},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"max_event_bytes": 0},
        {"max_request_bytes": 0},
    ],
)
def test_bad_settings_rejected(values):
    with pytest.raises(ValueError):
        Settings(**values)


def test_provider_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_URL", "https://provider.example/v1/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "server-only-secret")
    settings = Settings.from_env()
    assert settings.model == "test-model"
    assert settings.api_key == "server-only-secret"
    assert settings.provider_url.startswith("https://provider.example/")
