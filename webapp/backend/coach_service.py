"""Coach chat turn orchestration: selected provider, persistence, and engine."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from . import (
    coach_commands,
    coach_confirmations,
    coach_store,
    config,
    provider_commands,
    provider_store,
)
from .coach_engine import CoachTurn, complete_turn_async

log = logging.getLogger(__name__)


async def soft_start_provider_error(x_user_id: str) -> str | None:
    """Return a stable gate code before a deterministic Coach soft start."""
    from .health import build_coach_runtime_status

    runtime_status = await build_coach_runtime_status()
    if not runtime_status["ready_for_fast_path"]:
        return "coach_runtime_unavailable"
    profile = await provider_store.get_default_runtime_profile(x_user_id)
    if not provider_store.runtime_profile_configured(profile):
        return "provider_unconfigured"
    if bool(profile and profile.get("credential_needs_reauth")):
        return "provider_reauthentication_required"
    return None


@dataclass
class CoachChatResult:
    reply: Optional[str]
    notes: list[str]
    assistant_content: str
    tool_events: list[dict]
    context: Optional[dict] = None
    status: str = "succeeded"
    error: dict | None = None
    timing: dict[str, Any] | None = None


def _reachable_context_refs(context: object) -> set[str]:
    """Seed a bridge only with refs already visible in this turn's context."""
    if not isinstance(context, dict):
        return set()
    refs: set[str] = set()
    if context.get("schema_version") == "coach_turn_context.v1":
        for item in context.get("contexts", []):
            if not isinstance(item, dict):
                continue
            refs.update(_reachable_context_refs(item.get("projection")))
            refs.update(_reachable_context_refs(item.get("comparison_projection")))
            for key in ("context_ref", "analysis_ref", "comparison_analysis_ref", "target_ref"):
                value = item.get(key)
                if isinstance(value, str):
                    refs.add(value)
        return refs
    analysis_ref = context.get("analysis_ref")
    if isinstance(analysis_ref, dict):
        analysis_id = analysis_ref.get("analysis_id")
        if isinstance(analysis_id, str) and analysis_id.startswith("analysis:"):
            refs.add(analysis_id)

    evidence = context.get("evidence_summary")
    if isinstance(evidence, dict):
        for ref in evidence.get("segment_refs", []):
            if isinstance(ref, str) and ref.startswith("analysis:") and ":segment:" in ref:
                refs.add(ref)

    run_facts = context.get("run_facts")
    if isinstance(run_facts, dict):
        for summary in run_facts.get("section_summaries", []):
            if not isinstance(summary, dict):
                continue
            ref = summary.get("section_ref")
            if isinstance(ref, str) and ref.startswith("analysis:") and ":facts:" in ref:
                refs.add(ref)
    processed = context.get("processed_events")
    if isinstance(processed, dict):
        for table in processed.get("tables", []):
            if not isinstance(table, dict):
                continue
            ref = table.get("table_ref")
            if isinstance(ref, str) and ref.startswith("analysis:") and ":table:" in ref:
                refs.add(ref)
    return refs


async def run_chat_turn(
    *,
    x_user_id: str,
    thread_id: int,
    prior_messages: list[dict],
    user_msg_to_store: str,
    diagnosis: Any | None,
    diagnostic_context: dict | None = None,
    context_refs: list[dict] | None = None,
    legacy_session_id: Optional[int],
    cost_session_id: Optional[int],
    tool_bridge_endpoint: Optional[str] = None,
    desktop_token: Optional[str] = None,
    persist: bool = True,
    user_message_id: int | None = None,
    agent_run_ref: str | None = None,
    teaching_turn: Mapping[str, Any] | None = None,
    temporary_profile_refs: Mapping[str, str] | None = None,
    on_partial: Any | None = None,
) -> CoachChatResult:
    from .coach_context import coerce_coach_diagnostic_context

    user_msg_to_store, discovered_profile_refs = coach_commands.prepare_temporary_steam_profiles(
        user_msg_to_store,
    )
    profile_refs = dict(discovered_profile_refs)
    if temporary_profile_refs is not None:
        profile_refs.update(temporary_profile_refs)
    safe_prior_messages: list[dict] = []
    for message in prior_messages:
        if not isinstance(message, dict):
            continue
        safe_message = dict(message)
        if isinstance(safe_message.get("content"), str):
            safe_message["content"] = coach_commands.redact_temporary_steam_profiles(
                safe_message["content"],
            )
        safe_prior_messages.append(safe_message)

    context = (
        diagnostic_context
        if diagnostic_context is not None
        else coerce_coach_diagnostic_context(diagnosis)
    )
    from .coach_runtime import normalize_teaching_turn

    normalized_teaching_turn = normalize_teaching_turn(teaching_turn)
    provider_profile = await provider_store.get_default_runtime_profile(x_user_id)

    # No selected profile is a recoverable Coach state. Persist the user's
    # message and a stable note, but do not charge budget or invoke legacy
    # providers behind their back.
    if (
        not provider_store.runtime_profile_configured(provider_profile)
        or bool(provider_profile and provider_profile.get("credential_needs_reauth"))
    ):
        notes = ["LLM Provider 未配置，请先在 Provider Settings 完成连接测试"]
        assistant_content = "(Coach Provider 未配置，暂未生成回复)"
        if persist:
            await coach_store.append_message(
                thread_id, "user", user_msg_to_store,
                legacy_session_id=legacy_session_id,
                context=context,
                context_refs=context_refs,
            )
            await coach_store.append_message(
                thread_id, "assistant", assistant_content,
                legacy_session_id=legacy_session_id,
                context=context,
                context_refs=context_refs,
            )
        return CoachChatResult(
            reply=None,
            notes=notes,
            assistant_content=assistant_content,
            tool_events=[],
            context=context,
            status="failed",
            error={
                "domain": "permission",
                "code": "provider_unconfigured",
                "message": "Coach Provider is not configured",
                "retryable": False,
            },
        )

    if provider_profile is not None:
        credential = provider_profile.get("credential")
        if provider_commands.credential_requires_refresh(credential):
            try:
                refreshed = await provider_commands.refresh_for_coach(
                    x_user_id, int(provider_profile["profile_id"]),
                )
            except Exception as error:
                from .coach_runtime import redact_provider_secrets

                notes = [
                    "Provider credential 已过期，自动刷新失败，请重新认证",
                ]
                log.warning(
                    "coach credential refresh failed user=%s error=%s",
                    x_user_id,
                    redact_provider_secrets(str(error), provider_profile),
                )
                assistant_content = "(Coach Provider credential 已过期，暂未生成回复)"
                if persist:
                    await coach_store.append_message(
                        thread_id, "user", user_msg_to_store,
                        legacy_session_id=legacy_session_id, context=context,
                        context_refs=context_refs,
                    )
                    await coach_store.append_message(
                        thread_id, "assistant", assistant_content,
                        legacy_session_id=legacy_session_id, context=context,
                        context_refs=context_refs,
                    )
                return CoachChatResult(
                    None, notes, assistant_content, [], context, "failed",
                    {
                        "domain": "permission", "code": "credential_refresh_failed",
                        "message": "Provider credential refresh failed", "retryable": True,
                    },
                )
            if refreshed.get("status") != "succeeded":
                notes = ["Provider credential 刷新未完成，请重新认证"]
                assistant_content = "(Coach Provider credential 刷新未完成，暂未生成回复)"
                if persist:
                    await coach_store.append_message(
                        thread_id, "user", user_msg_to_store,
                        legacy_session_id=legacy_session_id, context=context,
                        context_refs=context_refs,
                    )
                    await coach_store.append_message(
                        thread_id, "assistant", assistant_content,
                        legacy_session_id=legacy_session_id, context=context,
                        context_refs=context_refs,
                    )
                return CoachChatResult(
                    None, notes, assistant_content, [], context, "failed",
                    {
                        "domain": "permission", "code": "credential_refresh_incomplete",
                        "message": "Provider credential refresh is incomplete", "retryable": True,
                    },
                )
            provider_profile = await provider_store.get_default_runtime_profile(x_user_id)
            if not provider_store.runtime_profile_configured(provider_profile):
                notes = ["Provider credential 刷新后仍不可用，请重新认证"]
                assistant_content = "(Coach Provider credential 不可用，暂未生成回复)"
                if persist:
                    await coach_store.append_message(
                        thread_id, "user", user_msg_to_store,
                        legacy_session_id=legacy_session_id, context=context,
                        context_refs=context_refs,
                    )
                    await coach_store.append_message(
                        thread_id, "assistant", assistant_content,
                        legacy_session_id=legacy_session_id, context=context,
                        context_refs=context_refs,
                    )
                return CoachChatResult(
                    None, notes, assistant_content, [], context, "failed",
                    {
                        "domain": "permission", "code": "credential_unavailable",
                        "message": "Provider credential is unavailable", "retryable": False,
                    },
                )

    if persist:
        user_message_id = await coach_store.append_message(
            thread_id, "user", user_msg_to_store,
            legacy_session_id=legacy_session_id, context=context,
            context_refs=context_refs,
        )
    elif user_message_id is None:
        raise ValueError("user_message_id is required when persistence is disabled")

    notes: list[str] = []
    reply: Optional[str] = None
    tool_events: list[dict] = []
    status = "succeeded"
    failure: dict | None = None
    timing: dict[str, Any] | None = None
    tool_bridge: dict | None = None
    try:
        if tool_bridge_endpoint:
            tool_bridge = coach_commands.issue_tool_bridge(
                x_user_id,
                thread_id,
                f"coach_message:{user_message_id}",
                tool_bridge_endpoint,
                desktop_token=desktop_token or None,
                ttl_seconds=min(config.COACH_RUNTIME_TIMEOUT_SECONDS, 900),
                reachable_refs=_reachable_context_refs(context),
                temporary_profile_refs=profile_refs,
            )
        turn = CoachTurn(
            prior_messages=safe_prior_messages,
            user_message=user_msg_to_store,
            diagnostic_context=context,
            user_id=x_user_id,
            provider_profile=provider_profile,
            tool_bridge=tool_bridge,
            teaching_turn=normalized_teaching_turn,
            run_ref=agent_run_ref,
            on_partial=on_partial,
        )
        engine_result = await complete_turn_async(turn)
        reply = (
            coach_commands.redact_temporary_steam_profiles(engine_result.reply)
            if engine_result.reply is not None
            else None
        )
        notes = list(engine_result.notes)
        tool_events = list(engine_result.tool_events)
        if coach_commands.contains_temporary_steam_profile(tool_events):
            tool_events = []
            raise ValueError("Coach tool event contains a Steam identity")
        status = engine_result.status
        failure = engine_result.error
        timing = engine_result.timing
    except Exception as e:
        # Keep provider credentials out of both logs and the persisted/API note.
        log.warning("coach chat 失败 user=%s error=%s", x_user_id, type(e).__name__)
        from .coach_runtime import redact_provider_secrets

        message = redact_provider_secrets(str(e), provider_profile)
        notes.append(f"对话失败: {message}")
        status = "failed"
        failure = {
            "domain": "model",
            "code": "generation_failed",
            "message": "Coach generation failed",
            "retryable": True,
        }
    finally:
        if tool_bridge is not None:
            await coach_commands.revoke_tool_bridge(tool_bridge["bearer_token"])

    try:
        confirmation_events = await coach_confirmations.sync_product_command_confirmations(
            x_user_id, f"coach_message:{user_message_id}",
        )
        pending_by_command = {
            event["command_id"]: event
            for event in confirmation_events
            if isinstance(event.get("command_id"), str)
        }
        merged_events: list[dict] = []
        for event in tool_events:
            command_id = event.get("command_id") if isinstance(event, dict) else None
            merged_events.append(pending_by_command.pop(command_id, event))
        merged_events.extend(pending_by_command.values())
        tool_events = merged_events
    except Exception:
        log.exception("coach confirmation projection failed user=%s", x_user_id)
        status = "failed"
        failure = {
            "domain": "tool",
            "code": "confirmation_projection_failed",
            "message": "Coach action confirmation is temporarily unavailable",
            "retryable": True,
        }

    assistant_content = reply if reply is not None else "(本次未能生成回复,见 notes)"
    if persist:
        await coach_store.append_message(
            thread_id, "assistant", assistant_content,
            trace=tool_events, legacy_session_id=legacy_session_id,
            context=context, context_refs=context_refs,
        )
    # cost_session_id remains in the compatibility call signature. Selected
    # providers do not share a trustworthy CNY pricing/usage contract, so active
    # turns neither estimate DeepSeek cost nor mutate legacy llm_cost_cny.

    return CoachChatResult(
        reply=reply,
        notes=notes,
        assistant_content=assistant_content,
        tool_events=tool_events,
        context=context,
        status=status,
        error=failure,
        timing=timing,
    )
