"""Coach LLM backends: Pi subprocess vs Python agent (Protocol + fallback)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

from . import config
from .config import LLM_PROVIDER

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoachTurn:
    """One chat turn input for coach engines."""

    prior_messages: Sequence[dict]
    user_message: str
    diagnosis: Any | None = None
    diagnostic_context: dict | None = None
    user_id: str = "dev"
    provider_profile: Mapping[str, Any] | None = field(default=None, repr=False)
    tool_bridge: Mapping[str, Any] | None = field(default=None, repr=False)


def _empty_coach_diagnosis():
    """无可用分析时的占位诊断(空 issues/summary,不编造指标)。"""
    from kovaak_tracker.coach.diagnosis import CoachDiagnosis, ProfileMatch

    return CoachDiagnosis(
        profile=ProfileMatch("unclassified", "未分类", 0.0, []),
        issues=[],
        summary={},
        comparison=None,
        meta={"analysis_context": "none"},
    )


def load_backend_or_none():
    """加载 legacy LLM backend;失败返回 None(兼容路径)。"""
    try:
        from kovaak_tracker.coach.providers import load_backend

        return load_backend(LLM_PROVIDER)
    except Exception as e:
        _log.warning("load_backend 失败,chat 走降级: %s", type(e).__name__)
        return None


def load_backend_for_profile(profile: Mapping[str, Any] | None):
    """Build the Python fallback from the selected profile, never from another provider."""
    if not profile:
        return None
    base_url = profile.get("base_url")
    credential = profile.get("credential")
    api_key = profile.get("api_key")
    if isinstance(credential, Mapping):
        candidate = credential.get("key", credential.get("access"))
        if isinstance(candidate, str):
            api_key = candidate
    model_id = profile.get("model_id")
    if not all(isinstance(value, str) and value.strip() for value in (base_url, api_key, model_id)):
        return None
    try:
        from kovaak_tracker.coach.providers import OpenAICompatBackend

        return OpenAICompatBackend(
            base_url=base_url,
            api_key=api_key,
            model=model_id,
        )
    except Exception as error:
        _log.warning("selected profile Python fallback unavailable: %s", type(error).__name__)
        return None


def _pi_turn_messages(
    prior_messages: Sequence[dict],
    user_message: str,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": m["role"], "content": m["content"]}
        for m in prior_messages
        if m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
    ]
    messages.append({"role": "user", "content": user_message})
    return messages


class CoachEngine(Protocol):
    def complete(self, turn: CoachTurn) -> str: ...


class PiCoachEngine:
    def complete(self, turn: CoachTurn):
        from .coach_context import (
            coerce_coach_diagnostic_context,
            serialize_coach_diagnostic_context,
        )
        from .coach_runtime import run_pi_coach_turn

        pi_messages = _pi_turn_messages(turn.prior_messages, turn.user_message)
        context = coerce_coach_diagnostic_context(
            turn.diagnostic_context if turn.diagnostic_context is not None else turn.diagnosis
        )
        analysis_summary = serialize_coach_diagnostic_context(context)
        return run_pi_coach_turn(
            user_id=turn.user_id,
            profile=turn.provider_profile,
            messages=pi_messages,
            analysis_summary=analysis_summary,
            tool_bridge=turn.tool_bridge,
            return_result=True,
        )


class PythonCoachEngine:
    def complete_with_notes(self, turn: CoachTurn) -> tuple[Optional[str], list[str]]:
        from kovaak_tracker.coach.agent import ChatMessage, chat_with_coach

        notes: list[str] = []
        from .coach_context import (
            coerce_coach_diagnostic_context,
            diagnostic_context_to_coach_diagnosis,
        )

        context = coerce_coach_diagnostic_context(
            turn.diagnostic_context if turn.diagnostic_context is not None else turn.diagnosis
        )
        diagnosis = (
            diagnostic_context_to_coach_diagnosis(context)
            if context is not None else _empty_coach_diagnosis()
        )
        chat_history = [
            ChatMessage(role=m["role"], content=m["content"])
            for m in turn.prior_messages
            if m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ]
        chat_history.append(ChatMessage(role="user", content=turn.user_message))

        backend = (
            load_backend_for_profile(turn.provider_profile)
            if turn.provider_profile is not None
            else load_backend_or_none()
        )
        if backend is None:
            notes.append("LLM 后端不可用,本次未生成回复")
            return None, notes
        reply = chat_with_coach(diagnosis, chat_history, backend)
        if reply is None:
            notes.append("agent 未在限定轮次内产出回复")
        return reply, notes


def _coach_runtime_fallback_python_enabled() -> bool:
    return config.COACH_RUNTIME_FALLBACK_PYTHON.lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@dataclass
class EngineCompleteResult:
    reply: Optional[str]
    notes: list[str]
    tool_events: list[dict[str, Any]] = field(default_factory=list)


class RuntimeRoutingCoachEngine:
    """COACH_RUNTIME=pi|python with optional Pi → Python fallback."""

    def __init__(
        self,
        *,
        pi: PiCoachEngine | None = None,
        python: PythonCoachEngine | None = None,
    ) -> None:
        self._pi = pi or PiCoachEngine()
        self._python = python or PythonCoachEngine()

    def complete_with_notes(self, turn: CoachTurn) -> EngineCompleteResult:
        notes: list[str] = []
        reply: Optional[str] = None

        if config.COACH_RUNTIME == "pi":
            from .coach_runtime import CoachRuntimeError

            try:
                pi_result = self._pi.complete(turn)
                if isinstance(pi_result, str):
                    return EngineCompleteResult(reply=pi_result, notes=notes)
                return EngineCompleteResult(
                    reply=pi_result.reply,
                    notes=list(pi_result.notes),
                    tool_events=list(pi_result.tool_events),
                )
            except CoachRuntimeError as e:
                from .coach_runtime import redact_provider_secrets

                message = redact_provider_secrets(str(e), turn.provider_profile)
                _log.warning("run_pi_coach_turn 失败: %s", message)
                notes.append(f"Pi coach-runtime 失败: {message}")
                tool_events = list(e.tool_events)
                unsafe_to_rerun = bool(tool_events) or e.side_effects_possible
                if _coach_runtime_fallback_python_enabled() and not unsafe_to_rerun:
                    notes.append("已回退 Python coach")
                    reply, py_notes = self._python.complete_with_notes(turn)
                    notes.extend(py_notes)
                elif unsafe_to_rerun:
                    notes.append("本轮可能已执行产品工具，未整轮回退；请先检查当前状态")
                return EngineCompleteResult(
                    reply=reply,
                    notes=notes,
                    tool_events=tool_events,
                )

        reply, py_notes = self._python.complete_with_notes(turn)
        notes.extend(py_notes)
        return EngineCompleteResult(reply=reply, notes=notes)


_configured_engine: RuntimeRoutingCoachEngine | None = None


def get_configured_engine() -> RuntimeRoutingCoachEngine:
    global _configured_engine
    if _configured_engine is None:
        _configured_engine = RuntimeRoutingCoachEngine()
    return _configured_engine


async def complete_turn_async(turn: CoachTurn) -> EngineCompleteResult:
    engine = get_configured_engine()
    return await asyncio.to_thread(engine.complete_with_notes, turn)
