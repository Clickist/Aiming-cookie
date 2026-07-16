"""Coach chat turn orchestration: selected provider, persistence, and engine."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from . import coach_commands, coach_store, config, provider_commands, provider_store
from .coach_engine import CoachTurn, complete_turn_async

log = logging.getLogger(__name__)


@dataclass
class CoachChatResult:
    reply: Optional[str]
    notes: list[str]
    assistant_content: str
    tool_events: list[dict]
    context: Optional[dict] = None


async def run_chat_turn(
    *,
    x_user_id: str,
    thread_id: int,
    prior_messages: list[dict],
    user_msg_to_store: str,
    diagnosis: Any | None,
    legacy_session_id: Optional[int],
    cost_session_id: Optional[int],
    tool_bridge_endpoint: Optional[str] = None,
    desktop_token: Optional[str] = None,
) -> CoachChatResult:
    from .coach_context import coerce_coach_diagnostic_context

    context = coerce_coach_diagnostic_context(diagnosis)
    provider_profile = (
        await provider_store.get_default_runtime_profile(x_user_id)
        if config.COACH_RUNTIME == "pi"
        else None
    )

    # No selected profile is a recoverable Coach state. Persist the user's
    # message and a stable note, but do not charge budget or invoke legacy
    # providers behind their back.
    if config.COACH_RUNTIME == "pi" and (
        not provider_store.runtime_profile_configured(provider_profile)
        or bool(provider_profile and provider_profile.get("credential_needs_reauth"))
    ):
        notes = ["LLM Provider 未配置，请先在 Provider Settings 完成连接测试"]
        assistant_content = "(Coach Provider 未配置，暂未生成回复)"
        await coach_store.append_message(
            thread_id,
            "user",
            user_msg_to_store,
            legacy_session_id=legacy_session_id,
            context=context,
        )
        await coach_store.append_message(
            thread_id,
            "assistant",
            assistant_content,
            legacy_session_id=legacy_session_id,
            context=context,
        )
        return CoachChatResult(
            reply=None,
            notes=notes,
            assistant_content=assistant_content,
            tool_events=[],
            context=context,
        )

    if config.COACH_RUNTIME == "pi" and provider_profile is not None:
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
                await coach_store.append_message(
                    thread_id,
                    "user",
                    user_msg_to_store,
                    legacy_session_id=legacy_session_id,
                    context=context,
                )
                await coach_store.append_message(
                    thread_id,
                    "assistant",
                    assistant_content,
                    legacy_session_id=legacy_session_id,
                    context=context,
                )
                return CoachChatResult(None, notes, assistant_content, [], context)
            if refreshed.get("status") != "succeeded":
                notes = ["Provider credential 刷新未完成，请重新认证"]
                assistant_content = "(Coach Provider credential 刷新未完成，暂未生成回复)"
                await coach_store.append_message(
                    thread_id,
                    "user",
                    user_msg_to_store,
                    legacy_session_id=legacy_session_id,
                    context=context,
                )
                await coach_store.append_message(
                    thread_id,
                    "assistant",
                    assistant_content,
                    legacy_session_id=legacy_session_id,
                    context=context,
                )
                return CoachChatResult(None, notes, assistant_content, [], context)
            provider_profile = await provider_store.get_default_runtime_profile(x_user_id)
            if not provider_store.runtime_profile_configured(provider_profile):
                notes = ["Provider credential 刷新后仍不可用，请重新认证"]
                assistant_content = "(Coach Provider credential 不可用，暂未生成回复)"
                await coach_store.append_message(
                    thread_id,
                    "user",
                    user_msg_to_store,
                    legacy_session_id=legacy_session_id,
                    context=context,
                )
                await coach_store.append_message(
                    thread_id,
                    "assistant",
                    assistant_content,
                    legacy_session_id=legacy_session_id,
                    context=context,
                )
                return CoachChatResult(None, notes, assistant_content, [], context)

    user_message_id = await coach_store.append_message(
        thread_id,
        "user",
        user_msg_to_store,
        legacy_session_id=legacy_session_id,
        context=context,
    )

    notes: list[str] = []
    reply: Optional[str] = None
    tool_events: list[dict] = []
    tool_bridge: dict | None = None
    try:
        if config.COACH_RUNTIME == "pi" and tool_bridge_endpoint:
            tool_bridge = coach_commands.issue_tool_bridge(
                x_user_id,
                thread_id,
                f"coach_message:{user_message_id}",
                tool_bridge_endpoint,
                desktop_token=desktop_token or None,
                ttl_seconds=min(config.COACH_RUNTIME_TIMEOUT_SECONDS, 900),
            )
        turn = CoachTurn(
            prior_messages=prior_messages,
            user_message=user_msg_to_store,
            diagnostic_context=context,
            user_id=x_user_id,
            provider_profile=provider_profile,
            tool_bridge=tool_bridge,
        )
        engine_result = await complete_turn_async(turn)
        reply = engine_result.reply
        notes = list(engine_result.notes)
        tool_events = list(engine_result.tool_events)
    except Exception as e:
        # Keep provider credentials out of both logs and the persisted/API note.
        log.warning("coach chat 失败 user=%s error=%s", x_user_id, type(e).__name__)
        from .coach_runtime import redact_provider_secrets

        message = redact_provider_secrets(str(e), provider_profile)
        notes.append(f"对话失败: {message}")
    finally:
        if tool_bridge is not None:
            await coach_commands.revoke_tool_bridge(tool_bridge["bearer_token"])

    assistant_content = reply if reply is not None else "(本次未能生成回复,见 notes)"
    await coach_store.append_message(
        thread_id,
        "assistant",
        assistant_content,
        trace=tool_events,
        legacy_session_id=legacy_session_id,
        context=context,
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
    )
