import pytest

from mcp_gateway.config import DEMO_TOKENS, Settings


def test_no_implicit_demo_tokens(monkeypatch):
    monkeypatch.delenv("GATEWAY_TOKENS", raising=False)
    with pytest.raises(ValueError, match="GATEWAY_TOKENS"):
        Settings.from_env()
    assert Settings.from_env(demo=True).tokens == DEMO_TOKENS


@pytest.mark.parametrize(
    "raw", ["{", "[]", "{}", '{"x":"superuser"}', '{"":"admin"}', '{"x": []}', '{"x": null}']
)
def test_bad_token_configuration(monkeypatch, raw):
    monkeypatch.setenv("GATEWAY_TOKENS", raw)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKENS", '{"opaque-secret":"viewer"}')
    monkeypatch.setenv("MCP_DOWNSTREAM_URL", "http://127.0.0.1:9001/mcp")
    settings = Settings.from_env()
    assert settings.tokens == {"opaque-secret": "viewer"}
    assert settings.downstream_url.endswith(":9001/mcp")


@pytest.mark.parametrize("url", ["file:///tmp/socket", "http://", "http://secret:pw@host/mcp"])
def test_invalid_downstream_url(url):
    with pytest.raises(ValueError):
        Settings(tokens=DEMO_TOKENS, downstream_url=url)
