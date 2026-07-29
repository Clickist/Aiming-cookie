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
    teaching_turn: Mapping[str, Any] | None = field(default=None, repr=False)
    run_ref: str | None = None


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
    @staticmethod
    def _analysis_summary(value: object) -> str | None:
        from .coach_context import (
            coerce_coach_diagnostic_context,
            serialize_coach_diagnostic_context,
        )
        from .coach_context_refs import coerce_context_bundle

        bundle = coerce_context_bundle(value)
        if bundle is not None:
            import json

            return json.dumps(
                bundle, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            )
        return serialize_coach_diagnostic_context(
            coerce_coach_diagnostic_context(value)
        )

    def complete(self, turn: CoachTurn):
        from .coach_runtime import run_pi_coach_turn

        pi_messages = _pi_turn_messages(turn.prior_messages, turn.user_message)
        analysis_summary = self._analysis_summary(
            turn.diagnostic_context if turn.diagnostic_context is not None else turn.diagnosis
        )
        return run_pi_coach_turn(
            user_id=turn.user_id,
            profile=turn.provider_profile,
            messages=pi_messages,
            analysis_summary=analysis_summary,
            tool_bridge=turn.tool_bridge,
            teaching_turn=turn.teaching_turn,
            return_result=True,
        )

    async def complete_async(self, turn: CoachTurn):
        from .coach_runtime import run_pi_coach_turn_async

        return await run_pi_coach_turn_async(
            user_id=turn.user_id,
            profile=turn.provider_profile,
            messages=_pi_turn_messages(turn.prior_messages, turn.user_message),
            analysis_summary=self._analysis_summary(
                turn.diagnostic_context if turn.diagnostic_context is not None else turn.diagnosis
            ),
            tool_bridge=turn.tool_bridge,
            teaching_turn=turn.teaching_turn,
            run_id=turn.run_ref,
        )


class PythonCoachEngine:
    def complete_with_notes(self, turn: CoachTurn) -> tuple[Optional[str], list[str]]:
        from kovaak_tracker.coach.agent import ChatMessage, chat_with_coach

        notes: list[str] = []
        from .coach_context import (
            coerce_coach_diagnostic_context,
            diagnostic_context_to_coach_diagnosis,
        )

        raw_context = turn.diagnostic_context if turn.diagnostic_context is not None else turn.diagnosis
        from .coach_context_refs import coerce_context_bundle

        bundle = coerce_context_bundle(raw_context)
        context = coerce_coach_diagnostic_context(
            bundle["contexts"][0]["projection"] if bundle and bundle["contexts"] else raw_context
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
    status: str = "succeeded"
    error: dict[str, Any] | None = None


def _runtime_failure(error) -> tuple[str, dict[str, Any]]:
    code = getattr(error, "error_code", "runtime_failed")
    category = getattr(error, "error_category", "coach_runtime")
    if code == "stopped":
        return "stopped", {
            "domain": "model", "code": code, "message": "Coach generation stopped",
            "retryable": True,
        }
    if category in {"network", "network_cloud"} or code in {
        "sidecar_unreachable", "sidecar_http_error", "runtime_timeout",
    }:
        domain = "network"
    elif category in {"permission", "provider_auth"} or code in {
        "invalid_profile", "unknown_provider", "unknown_model", "provider_unconfigured",
    }:
        domain = "permission"
    elif getattr(error, "tool_events", None):
        domain = "tool"
    else:
        domain = "model"
    return "failed", {
        "domain": domain,
        "code": code,
        "message": str(error),
        "retryable": bool(getattr(error, "retryable", True)),
    }


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
                    if reply is not None:
                        return EngineCompleteResult(reply=reply, notes=notes)
                elif unsafe_to_rerun:
                    notes.append("本轮可能已执行产品工具，未整轮回退；请先检查当前状态")
                return EngineCompleteResult(
                    reply=reply,
                    notes=notes,
                    tool_events=tool_events,
                    status=_runtime_failure(e)[0],
                    error=_runtime_failure(e)[1],
                )

        reply, py_notes = self._python.complete_with_notes(turn)
        notes.extend(py_notes)
        return EngineCompleteResult(reply=reply, notes=notes)

    async def complete_with_notes_async(self, turn: CoachTurn) -> EngineCompleteResult:
        if config.COACH_RUNTIME != "pi":
            return await asyncio.to_thread(self.complete_with_notes, turn)
        from .coach_runtime import CoachRuntimeError, redact_provider_secrets

        try:
            result = await self._pi.complete_async(turn)
            return EngineCompleteResult(
                reply=result.reply,
                notes=list(result.notes),
                tool_events=list(result.tool_events),
            )
        except CoachRuntimeError as error:
            message = redact_provider_secrets(str(error), turn.provider_profile)
            _log.warning("run_pi_coach_turn_async 失败: %s", message)
            status, failure = _runtime_failure(error)
            reply = getattr(error, "partial_reply", None)
            notes = [f"Pi coach-runtime 失败: {message}"]
            tool_events = list(error.tool_events)
            unsafe_to_rerun = bool(tool_events) or error.side_effects_possible
            if (
                status == "failed"
                and _coach_runtime_fallback_python_enabled()
                and not unsafe_to_rerun
            ):
                notes.append("已回退 Python coach")
                reply, py_notes = await asyncio.to_thread(
                    self._python.complete_with_notes, turn,
                )
                notes.extend(py_notes)
                if reply is not None:
                    return EngineCompleteResult(reply=reply, notes=notes)
            return EngineCompleteResult(
                reply=reply,
                notes=notes,
                tool_events=tool_events,
                status=status,
                error=failure,
            )


_configured_engine: RuntimeRoutingCoachEngine | None = None


def get_configured_engine() -> RuntimeRoutingCoachEngine:
    global _configured_engine
    if _configured_engine is None:
        _configured_engine = RuntimeRoutingCoachEngine()
    return _configured_engine


async def complete_turn_async(turn: CoachTurn) -> EngineCompleteResult:
    engine = get_configured_engine()
    return await engine.complete_with_notes_async(turn)
