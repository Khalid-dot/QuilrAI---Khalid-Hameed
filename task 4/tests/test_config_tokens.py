import pytest

from model_router.config import DEMO_KEYS, Provider, Settings
from model_router.tokens import TokenCounter


def test_default_assessment_settings():
    settings = Settings(api_keys=DEMO_KEYS)
    assert settings.tokens_per_window == 50_000
    assert settings.window_seconds == 60
    assert settings.primary_timeout_seconds == 3
    assert settings.secondary_timeout_seconds == 3


def test_explicit_encoding_counts_real_tokens_and_special_looking_input():
    counter = TokenCounter()
    assert counter.count("Hello world") == 2
    assert counter.count("") == 0
    assert counter.count("<|endoftext|>") > 0
    assert counter.count("Café ✅") == len(counter.encoding.encode_ordinary("Café ✅"))


def test_no_implicit_demo_keys(monkeypatch):
    monkeypatch.delenv("TENANT_API_KEYS", raising=False)
    with pytest.raises(ValueError):
        Settings.from_env()
    assert Settings.from_env(demo=True).api_keys == DEMO_KEYS


@pytest.mark.parametrize("value", ["{", "null", "{}", "[]", '[""]', "[123]", '["has space"]'])
def test_invalid_api_key_environment(monkeypatch, value):
    monkeypatch.setenv("TENANT_API_KEYS", value)
    with pytest.raises(ValueError):
        Settings.from_env()


@pytest.mark.parametrize(
    "values",
    [
        {"tokens_per_window": 0},
        {"tokens_per_window": True},
        {"window_seconds": 0},
        {"primary_timeout_seconds": float("nan")},
        {"secondary_timeout_seconds": float("inf")},
        {"database": ":memory:"},
    ],
)
def test_invalid_settings(values):
    with pytest.raises(ValueError):
        Settings(api_keys=DEMO_KEYS, **values)


@pytest.mark.parametrize("url", ["file:///tmp/socket", "http://", "http://secret:pw@provider/v1"])
def test_invalid_provider_url(url):
    with pytest.raises(ValueError):
        Provider(url, "test-model")


def test_environment_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("TENANT_API_KEYS", '["opaque-key"]')
    monkeypatch.setenv("PRIMARY_MODEL", "primary-model")
    monkeypatch.setenv("SECONDARY_API_KEY", "provider-only-key")
    settings = Settings.from_env(database=str(tmp_path / "custom.sqlite3"))
    assert settings.api_keys == ("opaque-key",)
    assert settings.primary.model == "primary-model"
    assert settings.secondary.api_key == "provider-only-key"
    assert settings.database == tmp_path / "custom.sqlite3"
