"""Coach chat turn orchestration: budget, persistence, engine, cost."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException

from . import coach_store, llm_budget, queue
from .coach_engine import CoachTurn, complete_turn_async

log = logging.getLogger(__name__)


@dataclass
class CoachChatResult:
    reply: Optional[str]
    notes: list[str]
    assistant_content: str


async def run_chat_turn(
    *,
    x_user_id: str,
    thread_id: int,
    prior_messages: list[dict],
    user_msg_to_store: str,
    diagnosis: Any | None,
    legacy_session_id: Optional[int],
    cost_session_id: Optional[int],
) -> CoachChatResult:
    from .worker import _estimate_llm_cost_cny

    chat_cost = _estimate_llm_cost_cny("", min_output_tokens=500)
    if not await llm_budget.check_and_record(x_user_id, chat_cost):
        raise HTTPException(429, "今日 LLM 预算已用尽,明天再聊")

    await coach_store.append_message(
        thread_id,
        "user",
        user_msg_to_store,
        legacy_session_id=legacy_session_id,
    )

    notes: list[str] = []
    reply: Optional[str] = None
    try:
        turn = CoachTurn(
            prior_messages=prior_messages,
            user_message=user_msg_to_store,
            diagnosis=diagnosis,
        )
        engine_result = await complete_turn_async(turn)
        reply = engine_result.reply
        notes = list(engine_result.notes)
    except Exception as e:
        log.exception("coach chat 失败 user=%s", x_user_id)
        notes.append(f"对话失败: {e}")

    assistant_content = reply if reply is not None else "(本次未能生成回复,见 notes)"
    await coach_store.append_message(
        thread_id,
        "assistant",
        assistant_content,
        legacy_session_id=legacy_session_id,
    )
    if reply is not None and cost_session_id is not None:
        await queue.add_llm_cost(cost_session_id, chat_cost)

    return CoachChatResult(
        reply=reply,
        notes=notes,
        assistant_content=assistant_content,
    )