# kovaak_tracker/coach/providers.py
"""LLM backends. Borrows pi's provider-skeleton design (categorize by API
protocol, config-driven, credential resolution) — no agent framework."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

_DEFAULT_CONFIG_PATH = str(Path(__file__).parent / "providers.json")


class LLMBackend(Protocol):
    def generate(self, system: str, user: str) -> str: ...


class AnthropicBackend:
    """anthropic-messages protocol (Claude)."""
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def generate(self, system: str, user: str) -> str:
        resp = self._client.messages.create(
            model=self._model, max_tokens=1024,
            system=system, messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text


class OpenAICompatBackend:
    """openai-completions protocol (local Ollama + future OpenAI-compatible)."""
    def __init__(self, base_url: str, api_key: str, model: str):
        import openai
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key or "ollama")
        self._model = model

    def generate(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content


def load_backend(provider: str = "anthropic", config_path: str | None = None,
                 config: dict | None = None) -> LLMBackend:
    cfg = config if config is not None else _load_config(config_path)
    if provider not in cfg:
        raise ValueError(f"unknown provider {provider!r}; have {list(cfg)}")
    p = cfg[provider]
    api_key = os.environ.get(p.get("api_key_env", ""), "")
    if provider == "anthropic":
        return AnthropicBackend(api_key=api_key, model=p["model"])
    return OpenAICompatBackend(base_url=p["base_url"], api_key=api_key, model=p["model"])


def _load_config(config_path):
    path = config_path or _DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        return json.load(f)
