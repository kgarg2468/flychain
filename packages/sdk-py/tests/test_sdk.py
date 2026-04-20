from __future__ import annotations

from flychain import __version__, gateway_base_url


def test_version_present() -> None:
    assert __version__ == "0.0.0"


def test_gateway_base_url_env_override(monkeypatch) -> None:
    monkeypatch.setenv("FLYCHAIN_GATEWAY_URL", "https://example.test:9999")
    assert gateway_base_url() == "https://example.test:9999"


def test_gateway_base_url_default(monkeypatch) -> None:
    monkeypatch.delenv("FLYCHAIN_GATEWAY_URL", raising=False)
    assert gateway_base_url() == "http://localhost:8080"


def test_gateway_base_url_custom_default(monkeypatch) -> None:
    monkeypatch.delenv("FLYCHAIN_GATEWAY_URL", raising=False)
    assert gateway_base_url("http://custom:1234") == "http://custom:1234"
