"""Coach LLM backends: Pi subprocess vs Python agent (Protocol + fallback)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence

from . import config
from .config import LLM_PROVIDER

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoachTurn:
    """One chat turn input for coach engines."""

    prior_messages: Sequence[dict]
    user_message: str
    diagnosis: Any | None = None


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
    """加载 LLM backend;失败返回 None(由 caller 决定降级文案)。"""
    try:
        from kovaak_tracker.coach.providers import load_backend

        return load_backend(LLM_PROVIDER)
    except Exception as e:
        _log.warning("load_backend 失败,chat 走降级: %s", e)
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
    def complete(self, turn: CoachTurn) -> str:
        from .coach_runtime import diagnosis_to_analysis_summary, run_pi_coach_turn

        pi_messages = _pi_turn_messages(turn.prior_messages, turn.user_message)
        analysis_summary = diagnosis_to_analysis_summary(turn.diagnosis)
        return run_pi_coach_turn(
            messages=pi_messages,
            analysis_summary=analysis_summary,
        )


class PythonCoachEngine:
    def complete_with_notes(self, turn: CoachTurn) -> tuple[Optional[str], list[str]]:
        from kovaak_tracker.coach.agent import ChatMessage, chat_with_coach

        notes: list[str] = []
        diagnosis = (
            turn.diagnosis if turn.diagnosis is not None else _empty_coach_diagnosis()
        )
        chat_history = [
            ChatMessage(role=m["role"], content=m["content"])
            for m in turn.prior_messages
            if m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ]
        chat_history.append(ChatMessage(role="user", content=turn.user_message))

        backend = load_backend_or_none()
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
                reply = self._pi.complete(turn)
                return EngineCompleteResult(reply=reply, notes=notes)
            except CoachRuntimeError as e:
                _log.warning("run_pi_coach_turn 失败: %s", e)
                notes.append(f"Pi coach-runtime 失败: {e}")
                if _coach_runtime_fallback_python_enabled():
                    notes.append("已回退 Python coach")
                    reply, py_notes = self._python.complete_with_notes(turn)
                    notes.extend(py_notes)
                return EngineCompleteResult(reply=reply, notes=notes)

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