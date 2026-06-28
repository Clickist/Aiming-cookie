# tests/coach/test_providers.py
import json, os, tempfile
from unittest import mock
from kovaak_tracker.coach import providers


def test_protocol_generate_contract():
    class Fake:
        def generate(self, system, user):
            return f"{system}|{user}"
    b = Fake()
    assert b.generate("s", "u") == "s|u"


def test_load_backend_reads_config(monkeypatch):
    cfg = {"anthropic": {"model": "m", "api_key_env": "K"},
           "local": {"base_url": "http://x/v1", "model": "q", "api_key_env": "L"}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f); cfg_path = f.name
    monkeypatch.setattr(providers, "_DEFAULT_CONFIG_PATH", cfg_path)

    # avoid real client construction: stub the backend classes
    with mock.patch.object(providers, "AnthropicBackend") as A, \
         mock.patch.object(providers, "OpenAICompatBackend") as O:
        providers.load_backend("anthropic")
        A.assert_called_once()
        providers.load_backend("local")
        O.assert_called_once_with(base_url="http://x/v1", api_key="", model="q")


def test_credential_resolution_from_env(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret123")
    monkeypatch.setattr(providers, "_DEFAULT_CONFIG_PATH", "/nonexistent.json")
    with mock.patch.object(providers, "AnthropicBackend") as A:
        providers.load_backend("anthropic",
                               config={"anthropic": {"model": "m", "api_key_env": "MY_KEY"}})
        A.assert_called_once_with(api_key="secret123", model="m")
