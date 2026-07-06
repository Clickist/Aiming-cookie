# kovaak_tracker/coach/providers.py
"""LLM backends. Borrows pi's provider-skeleton design (categorize by API
protocol, config-driven, credential resolution) — no agent framework."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, TypedDict

_DEFAULT_CONFIG_PATH = str(Path(__file__).parent / "providers.json")

# Network resilience for messages_create (agent loop path). Both anthropic
# and openai SDKs accept a ``timeout`` kwarg on create(); retry on transient
# errors so users don't lose narration to network jitter.
_DEFAULT_TIMEOUT_SEC = 60.0
_MAX_RETRIES = 2
_RETRY_BACKOFF_SEC = 1.5


class LLMBackend(Protocol):
    def generate(self, system: str, user: str) -> str: ...


# ---------------------------------------------------------------------------
# Tool-use capable backend abstraction (added for agent loop; see
# docs/superpowers/specs/2026-07-05-aiming-coach-agent-design.md §7).
# Existing LLMBackend.generate stays for narrator.py fallback compatibility.
# ---------------------------------------------------------------------------


class ToolCall(TypedDict):
    """One tool invocation requested by the LLM.

    Abstracts over Anthropic tool_use blocks and OpenAI tool_calls:
    backend implementations convert their native shape to this dict.
    """
    id: str                       # tool_use_id (Anthropic) / tool_call id (OpenAI)
    name: str                     # function/tool name
    arguments: dict[str, Any]     # parsed JSON arguments


@dataclass
class ToolUseResponse:
    """Backend-agnostic result of one ``messages_create`` call.

    content_text: concatenation of text blocks emitted by the model (may be
        empty when stop_reason == "tool_calls").
    tool_calls: parsed tool-use requests; empty list when model emitted no
        tool calls (i.e. stop_reason == "end_turn" / "stop").
    stop_reason: normalized stop reason. Recognized values:
        "end_turn" (model finished), "tool_calls" (model wants tools),
        "max_tokens" (hit token budget), "stop_sequence" (rare).
    raw: optional native response object (debug only; do not rely on shape).
    """
    content_text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    raw: Any = None


class ToolUseBackend(Protocol):
    """LLM backend supporting multi-turn tool use.

    Contract for :mod:`kovaak_tracker.coach.agent`. Backends translate their
    native SDK shapes (Anthropic messages / OpenAI chat.completions) into
    :class:`ToolUseResponse` so the agent loop is provider-agnostic.

    Messages use a minimal, stable shape::

        {"role": "user" | "assistant",
         "content": str | list[dict]}

    Tool-result blocks use::

        {"type": "tool_result",
         "tool_use_id": str,
         "content": str}

    Tools is a list of OpenAI-style function specs::

        {"type": "function",
         "function": {"name": ..., "description": ..., "parameters": {...}}}
    """

    def messages_create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> ToolUseResponse: ...


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

    def messages_create(
        self, *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> ToolUseResponse:
        # Convert normalized messages to Anthropic shape. The only reshape
        # needed: our assistant tool_use blocks use ``arguments``; Anthropic
        # uses ``input``. text / tool_result blocks already match natively.
        anthropic_messages: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            content = m.get("content")
            if isinstance(content, str):
                anthropic_messages.append({"role": role, "content": content})
                continue
            if isinstance(content, list):
                blocks: list[dict[str, Any]] = []
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        blocks.append({
                            "type": "tool_use",
                            "id": b.get("id", ""),
                            "name": b.get("name", ""),
                            "input": b.get("arguments", {}) or {},
                        })
                    else:
                        blocks.append(b)
                anthropic_messages.append({"role": role, "content": blocks})
                continue
        # Convert OpenAI-style function specs to Anthropic tool schema.
        anthropic_tools: list[dict[str, Any]] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            fn = t.get("function", t)
            anthropic_tools.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {
                    "type": "object", "properties": {},
                },
            })
        resp = _call_with_retry(lambda timeout: self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system,
            messages=anthropic_messages, tools=anthropic_tools or None,
            timeout=timeout,
        ))
        content_text = ""
        tool_calls: list[ToolCall] = []
        for block in (getattr(resp, "content", None) or []):
            btype = getattr(block, "type", None)
            if btype == "text":
                content_text += getattr(block, "text", "") or ""
            elif btype == "tool_use":
                tool_calls.append(ToolCall(
                    id=getattr(block, "id", "") or "",
                    name=getattr(block, "name", "") or "",
                    arguments=getattr(block, "input", {}) or {},
                ))
        raw_stop = getattr(resp, "stop_reason", "") or ""
        stop_reason = "tool_calls" if raw_stop == "tool_use" else (raw_stop or "end_turn")
        return ToolUseResponse(
            content_text=content_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw=resp,
        )


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

    def messages_create(
        self, *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> ToolUseResponse:
        return _openai_compat_messages_create(
            self._client, self._model,
            system=system, messages=messages,
            tools=tools, max_tokens=max_tokens,
        )


# Stop reasons normalized to ToolUseResponse.stop_reason vocabulary. Keys not
# present fall through to whatever the SDK returned (debug only).
_OPENAI_STOP_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "length": "max_tokens",
    "content_filter": "max_tokens",
}


def _parse_tool_args(raw: str) -> dict[str, Any]:
    """Parse OpenAI tool_call.function.arguments (JSON string). Empty on err."""
    if not raw:
        return {}
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _call_with_retry(fn, *, timeout: float = _DEFAULT_TIMEOUT_SEC):
    """Call ``fn(timeout=timeout)`` with simple retry on transient errors.

    Both the anthropic and openai SDKs accept a ``timeout`` keyword on their
    ``create()`` methods. Retries on any exception except HTTP 4xx client
    errors (which won't be fixed by retrying). Backoff: 1.5s then 3.0s.
    """
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn(timeout=timeout)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            is_4xx = isinstance(status, int) and 400 <= status < 500
            if attempt >= _MAX_RETRIES or is_4xx:
                raise
            time.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))


def _openai_compat_messages_create(
    client, model: str, *,
    system: str, messages: list[dict[str, Any]],
    tools: list[dict[str, Any]], max_tokens: int,
) -> ToolUseResponse:
    """Shared OpenAI-shape ``messages_create`` for DeepSeek + OpenAI-compat.

    Converts normalized messages (see :class:`ToolUseBackend` docstring) to
    OpenAI chat-completions shape, calls the API with retry + timeout, and
    parses the response into :class:`ToolUseResponse`.
    """
    # Convert our normalized messages to OpenAI chat-completions shape.
    # - user/assistant with string content -> passthrough
    # - assistant message carrying tool_calls (from a prior turn we built)
    #   -> message["tool_calls"] in OpenAI form
    # - user message with list content of tool_result blocks ->
    #   expanded into one OpenAI "tool" message per tool_result
    oai_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        role = m["role"]
        content = m.get("content")
        if isinstance(content, str):
            oai_messages.append({"role": role, "content": content})
            continue
        # list content: either our tool_result blocks (role=user) or
        # an assistant echo with tool_calls (role=assistant)
        if role == "assistant" and isinstance(content, list):
            tool_calls = [
                b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            text_parts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            oai_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(text_parts) or None,
            }
            if tool_calls:
                oai_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(
                                tc.get("arguments", {}), ensure_ascii=False
                            ),
                        },
                    }
                    for tc in tool_calls
                ]
            oai_messages.append(oai_msg)
            continue
        if role == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    oai_messages.append({
                        "role": "tool",
                        "tool_call_id": b["tool_use_id"],
                        "content": b.get("content", ""),
                    })
            continue
            # fallback: drop unrecognized shapes defensively
    resp = _call_with_retry(lambda timeout: client.chat.completions.create(
        model=model, messages=oai_messages,
        tools=tools or None, max_tokens=max_tokens, timeout=timeout,
    ))
    choice = resp.choices[0]
    msg = choice.message
    tool_calls: list[ToolCall] = []
    for tc in (getattr(msg, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        tool_calls.append(ToolCall(
            id=getattr(tc, "id", "") or "",
            name=getattr(fn, "name", "") if fn else "",
            arguments=_parse_tool_args(getattr(fn, "arguments", "") if fn else ""),
        ))
    raw_stop = getattr(choice, "finish_reason", "") or ""
    stop_reason = _OPENAI_STOP_MAP.get(raw_stop, raw_stop or "end_turn")
    return ToolUseResponse(
        content_text=getattr(msg, "content", "") or "",
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        raw=resp,
    )


class DeepSeekBackend:
    """DeepSeek via OpenAI-compatible SDK + function calling.

    DeepSeek-V3 supports OpenAI-style function calling; we point the OpenAI
    Python SDK at DeepSeek's base_url and reuse the chat.completions API.

    Config from env:
      - DEEPSEEK_API_KEY (required at construction)
      - DEEPSEEK_BASE_URL (default: official DeepSeek endpoint)
      - DEEPSEEK_MODEL (default: deepseek-chat)
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
    ):
        import openai
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    def generate(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""

    def messages_create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> ToolUseResponse:
        return _openai_compat_messages_create(
            self._client, self._model,
            system=system, messages=messages,
            tools=tools, max_tokens=max_tokens,
        )


def load_backend(provider: str = "anthropic", config_path: Optional[str] = None,
                 config: Optional[dict] = None) -> ToolUseBackend | LLMBackend:
    cfg = config if config is not None else _load_config(config_path)
    if provider not in cfg:
        raise ValueError(f"unknown provider {provider!r}; have {list(cfg)}")
    p = cfg[provider]
    api_key = os.environ.get(p.get("api_key_env", ""), "")
    if provider == "anthropic":
        return AnthropicBackend(api_key=api_key, model=p["model"])
    if provider == "deepseek":
        return DeepSeekBackend(
            api_key=api_key,
            base_url=p.get("base_url", "https://api.deepseek.com/v1"),
            model=p["model"],
        )
    return OpenAICompatBackend(base_url=p["base_url"], api_key=api_key, model=p["model"])


def _load_config(config_path):
    path = config_path or _DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        return json.load(f)
