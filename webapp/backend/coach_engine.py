"""Product Coach runtime backed by the Pi policy stack."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

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
    on_partial: Any | None = field(default=None, repr=False)


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
            on_partial=turn.on_partial,
        )


@dataclass
class EngineCompleteResult:
    reply: Optional[str]
    notes: list[str]
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "succeeded"
    error: dict[str, Any] | None = None
    timing: dict[str, Any] | None = None


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
    """Product Coach runtime. Failures stay in the selected Pi Provider path."""

    def __init__(
        self,
        *,
        pi: PiCoachEngine | None = None,
    ) -> None:
        self._pi = pi or PiCoachEngine()

    def complete_with_notes(self, turn: CoachTurn) -> EngineCompleteResult:
        notes: list[str] = []
        reply: Optional[str] = None

        from .coach_runtime import CoachRuntimeError

        try:
            pi_result = self._pi.complete(turn)
            if isinstance(pi_result, str):
                return EngineCompleteResult(reply=pi_result, notes=notes)
            return EngineCompleteResult(
                reply=pi_result.reply,
                notes=list(pi_result.notes),
                tool_events=list(pi_result.tool_events),
                timing=pi_result.timing,
            )
        except CoachRuntimeError as e:
            from .coach_runtime import redact_provider_secrets

            message = redact_provider_secrets(str(e), turn.provider_profile)
            _log.warning("run_pi_coach_turn 失败: %s", message)
            notes.append(f"Pi coach-runtime 失败: {message}")
            tool_events = list(e.tool_events)
            if tool_events or e.side_effects_possible:
                notes.append("本轮可能已执行产品工具；重试前请先检查当前状态")
            status, failure = _runtime_failure(e)
            return EngineCompleteResult(
                reply=reply,
                notes=notes,
                tool_events=tool_events,
                status=status,
                error=failure,
            )

    async def complete_with_notes_async(self, turn: CoachTurn) -> EngineCompleteResult:
        from .coach_runtime import CoachRuntimeError, redact_provider_secrets

        try:
            result = await self._pi.complete_async(turn)
            return EngineCompleteResult(
                reply=result.reply,
                notes=list(result.notes),
                tool_events=list(result.tool_events),
                timing=result.timing,
            )
        except CoachRuntimeError as error:
            message = redact_provider_secrets(str(error), turn.provider_profile)
            _log.warning("run_pi_coach_turn_async 失败: %s", message)
            status, failure = _runtime_failure(error)
            reply = getattr(error, "partial_reply", None)
            notes = [f"Pi coach-runtime 失败: {message}"]
            tool_events = list(error.tool_events)
            if tool_events or error.side_effects_possible:
                notes.append("本轮可能已执行产品工具；重试前请先检查当前状态")
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
