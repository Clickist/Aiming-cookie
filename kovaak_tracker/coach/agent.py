"""Coach agent loop: a tool-use agent that progressively retrieves knowledge
via tool calls.

设计参考 docs/superpowers/specs/2026-07-05-aiming-coach-agent-design.md。

三个入口（共用 loop，不同 system prompt + tool 子集）：
  * :func:`narrate_diagnosis` —— 画像 + 头号问题 + 根因 + 训练建议
  * :func:`narrate_progress` —— 趋势 + 多基准对比 + 下阶段重点
  * :func:`narrate_plan` —— 交错编排 / 退步 / 保持 / 休息的解释

防幻觉铁律：
  * 诊断 payload 是 ground truth，agent 没有"改诊断"tool。
  * 所有数值必须来自 payload 或 tool 返回的预备切片。
  * 知识 tool 只返回文档原文切片（带 source_ref + source_level）。

失败降级：loop 异常 / 超过 max_turns → 返回 ``None``（设计稿 §3 决策，
不 fallback 到单次 LLM——避免维护两套实现）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional

from .agent_tools import (
    ToolBundle,
    build_diagnosis_tools,
    build_plan_tools,
    build_progress_tools,
    diagnosis_payload,
)
from .diagnosis import CoachDiagnosis
from .planning import TrainingPlan
from .providers import ToolUseBackend, ToolUseResponse

_log = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 8
DEFAULT_MAX_TOKENS = 2048
MAX_TURNS_HARD_CAP = 12  # 兜底，防 caller 误设过大值卡死循环


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

DIAGNOSIS_SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练，精通运动学理论（min-jerk / Becker 减速段 / "
    "submovement / Fitts / SPARC）+ Voltaic 社区实践。\n\n"
    "核心理论锚：减速段是命中成败最强信号（Becker）；flick = 初始甩枪 + corrective 修正"
    "（submovement）；SPARC 度量减速平滑度；Fitts 速度-精度权衡。\n\n"
    "【讲解规则】：\n"
    "1. 中文，150-300 字。结构：流派画像 → 头号问题 + 根因（症状→物理→训练）→ "
    "最优先训练建议\n"
    "2. **英文术语必须配人话解释**——首次出现写成「中文（英文）」并一句话说清。例：\n"
    "   「减速段占比过高（decel_frac）——flick 后刹车那段拖太长」\n"
    "   「减速平滑度差（SPARC 低）——速度降得不顺、有抖动」\n"
    "3. 铁律：只基于提供的诊断数据讲解，不要编造任何指标数值或未给出的信息；"
    "数据缺失就略过。tool 返回的社区/理论片段仅供解释已给出的诊断、让建议更具体"
    "权威——禁止用它反推未给出的指标或信号\n"
    "4. 语气具体、可执行，不空话。可用比喻（如「蹭着瞄」「拖刹车」）但每条建议落到"
    "具体动作/场景\n\n"
    "【tool 使用指引】：\n"
    "你收到的 user message 是结构化诊断 payload（已定型，不可修改）。讲解前请用 tool "
    "calls 按需检索知识：\n"
    "- 对每个 priority=1 的 issue：调 coach_fetch_knowledge(signal) 拿社区归因\n"
    "- 想引用理论支撑：调 coach_fetch_kinematics / coach_fetch_prescription / "
    "coach_fetch_coaching_theory\n"
    "- 想给具体案例：调 coach_fetch_community_example\n"
    "- 不确定有哪些 key：先调 coach_list_signals / coach_list_knowledge_topics\n"
    "不要把所有 tool 都调一遍——只调你讲解需要的。调完 tool 后，基于诊断 payload + "
    "tool 返回的片段，写一段中文讲解。"
)


PROGRESS_SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练，精通运动学理论 + Voltaic 社区实践。\n"
    "你收到的 user message 是玩家的历史趋势 + 多基准对比数据（JSON）。请用 tool calls "
    "按需检索知识（coach_get_trend / coach_get_comparison / 各 fetch_* 知识 tool），"
    "然后用中文写一段进步解读（150-300 字）：\n"
    "1. 先总结进步方向（哪些指标改善了/退步了，引用趋势和对比 verdict）\n"
    "2. 再结合基线/上次/高手参考定位当前水平\n"
    "3. 最后给下一阶段训练重点\n\n"
    "**英文术语必须配人话解释**——首次出现写成「中文（英文）」并一句话说清。\n"
    "铁律：只基于提供的数据讲解，不要编造任何指标数值或未给出的信息；数据缺失就略过。"
)


PLAN_SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练，精通运动学理论 + contextual interference + "
    "Voltaic 社区实践。\n"
    "你收到的 user message 是玩家的训练计划结构（JSON）：焦点指标、调整项"
    "（交错/退步/保持/休息）、复测频率建议。请用 tool calls 按需检索知识"
    "（coach_get_plan / coach_fetch_coaching_theory(\"contextual_interference\" / "
    "\"deliberate_practice\" / \"guidance_hypothesis\") 等），然后用中文写一段"
    "「下次该怎么练」的讲解（150-300 字）：\n"
    "1. 先说清楚下阶段的训练重点（哪些指标停滞/退步→为什么换结构而非加量）\n"
    "2. 再给具体场景编排（强调交错而非磨单一场景）\n"
    "3. 最后提醒复测节奏与休息\n\n"
    "**英文术语必须配人话解释**——首次出现写成「中文（英文）」并一句话说清。\n"
    "铁律：只基于提供的计划数据讲解，不要编造任何指标数值或未给出的信息；数据缺失就略过。"
)


CHAT_SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练，正在和玩家**多轮对话**。玩家刚看完他们的"
    "诊断报告，现在在问你问题。\n\n"
    "诊断 payload 已作为 system prompt 的一部分提供——这是 ground truth，不可修改，"
    "也不要重新诊断。基于已有诊断 + 历史对话回答，问题不明确时可以追问。\n\n"
    "【回答规则】：\n"
    "1. 中文回答，简洁、可执行。简短问题就简短答，不要每次都写一大段\n"
    "2. **英文术语首次出现写成「中文（英文）」并一句话说清**。例：\n"
    "   「减速段占比（decel_frac）——flick 后刹车那段占总时间的比例」\n"
    "3. 铁律（继承自诊断讲解）：不要编造任何指标数值。要引用诊断里的数值时，调 "
    "coach_get_diagnosis / coach_get_meta 回查；要解释理论术语时，先调 "
    "coach_fetch_kinematics(topic) / coach_fetch_knowledge(signal) / "
    "coach_fetch_prescription(topic) 取定义再解释；不知道就说不知道\n"
    "4. 想推荐训练场景：调 coach_fetch_knowledge(signal) 或 "
    "coach_fetch_community_example(topic) 拿社区措辞，落到具体场景\n"
    "5. 玩家追问 SPARC / decel_frac / submovement 等术语时，先调 "
    "coach_fetch_kinematics 取定义，再用人话解释\n\n"
    "【tool 使用纪律】：只调你这次回答需要的 tool，不要每次都把工具调一遍。"
    "工具失败时降级——告诉玩家「这块内容暂时拿不到」并基于已知信息作答，不要硬编。"
)


@dataclass(frozen=True)
class ChatMessage:
    """一条多轮对话消息（user 或 assistant）。

    tool_calls_trace 仅供可观测/防幻觉（让前端看到 assistant 这一轮调了哪些
    tool）；不参与 backend.messages_create 的请求构造——后者只取 role + content。
    """
    role: Literal["user", "assistant"]
    content: str
    tool_calls_trace: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


class AgentError(Exception):
    """Raised internally; caught by narrate_* to map to None + trace."""


def run_agent_loop(
    backend: ToolUseBackend,
    system: str,
    user_payload: str,
    tools: ToolBundle,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    seed_messages: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Drive one agent conversation to completion.

    Returns a dict::

        {"narration": str | None, "trace": list[dict], "stop_reason": str}

    ``trace`` is one entry per tool call, for debug/observability. On failure
    (max_turns exceeded, exception, or empty output) returns
    ``{"narration": None, ..., "error": "..."}`` — never raises (best-effort).

    ``seed_messages``: when provided, replaces the default
    ``[{"role": "user", "content": user_payload}]`` seed. Used by
    :func:`chat_with_coach` to forward multi-turn chat history. The
    ``user_payload`` arg still works for backward compat (ignored when
    ``seed_messages`` is provided, but still required by signature).
    """
    turns_cap = min(max_turns, MAX_TURNS_HARD_CAP)
    if seed_messages is not None:
        messages: list[dict[str, Any]] = [
            {"role": m["role"], "content": m["content"]} for m in seed_messages
        ]
    else:
        messages = [{"role": "user", "content": user_payload}]
    trace: list[dict[str, Any]] = []
    last_text = ""

    try:
        for turn in range(turns_cap):
            resp: ToolUseResponse = backend.messages_create(
                system=system, messages=messages,
                tools=tools.schema_list(), max_tokens=max_tokens,
            )
            # If the model called tools, execute them and feed results back.
            # tool_calls alone is the trigger — some backends (e.g. DeepSeek on
            # long context) return tool_calls with stop_reason="end_turn"/
            # "max_tokens"; dropping them silently breaks the loop. stop_reason
            # is only consulted in the terminal branch below.
            if resp.tool_calls:
                # Append an assistant echo carrying the tool_use blocks so the
                # next call can resolve tool_call ids.
                assistant_content: list[dict[str, Any]] = []
                if resp.content_text:
                    assistant_content.append({"type": "text", "text": resp.content_text})
                tool_results: list[dict[str, Any]] = []
                for tc in resp.tool_calls:
                    result = tools.dispatch(tc["name"], tc.get("arguments", {}) or {})
                    result_str = json.dumps(result, ensure_ascii=False)
                    trace.append({
                        "turn": turn + 1,
                        "tool": tc["name"],
                        "arguments": tc.get("arguments", {}),
                        "result_preview": result_str[:200],
                    })
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": tc.get("arguments", {}),
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": result_str,
                    })
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})
                last_text = resp.content_text or last_text
                continue

            # No tool calls — treat as terminal.
            last_text = resp.content_text or last_text
            stop = resp.stop_reason or "end_turn"
            if stop == "max_tokens":
                # 截断文本可能不完整——丢弃,让 report.py 触发降级(spec §8)。
                return {
                    "narration": None,
                    "trace": trace,
                    "stop_reason": stop,
                    "warning": "max_tokens reached; narration discarded (may be truncated)",
                }
            return {
                "narration": last_text or None,
                "trace": trace,
                "stop_reason": stop,
            }

        # Exhausted turns without a terminal message. 半截 preamble
        # ("让我查一下...") 不能当 narration——丢弃,触发 report.py 降级(spec §8)。
        return {
            "narration": None,
            "trace": trace,
            "stop_reason": "max_turns_exceeded",
            "error": f"agent did not converge in {turns_cap} turns",
        }
    except Exception as e:  # best-effort: surface error, never raise
        _log.warning("agent loop failed: %s", e, exc_info=True)
        return {
            "narration": None,
            "trace": trace,
            "stop_reason": "exception",
            "error": f"{type(e).__name__}: {e}",
        }


# ---------------------------------------------------------------------------
# User payload serialization
# ---------------------------------------------------------------------------


def _serialize_diagnosis(diagnosis: CoachDiagnosis) -> str:
    return json.dumps(diagnosis_payload(diagnosis), ensure_ascii=False)


def _serialize_progress(trend: dict, comparison: list[dict]) -> str:
    payload = {
        "trend": {m: series for m, series in trend.items()},
        "comparison": comparison or [],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _serialize_plan(plan: TrainingPlan) -> str:
    payload = {
        "focus_metrics": list(plan.focus_metrics),
        "adjustments": [
            {"kind": a.kind, "target_metric": a.target_metric,
             "scenarios": [{"scenario": p.scenario, "reason": p.reason}
                           for p in a.scenarios],
             "reason": a.reason, "evidence": a.evidence}
            for a in plan.adjustments
        ],
        "schedule_note": plan.schedule_note,
        "evidence_anchors": list(plan.evidence_anchors),
        "notes": list(plan.notes),
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def narrate_diagnosis(
    diagnosis: CoachDiagnosis,
    backend: ToolUseBackend,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Optional[str]:
    """Diagnosis → 中文教练讲解 via tool-use agent."""
    tools = build_diagnosis_tools(diagnosis)
    user_payload = _serialize_diagnosis(diagnosis)
    out = run_agent_loop(
        backend, DIAGNOSIS_SYSTEM_PROMPT, user_payload, tools,
        max_turns=max_turns, max_tokens=max_tokens,
    )
    return out["narration"]


def narrate_progress(
    trend: dict,
    comparison: list[dict],
    backend: ToolUseBackend,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Optional[str]:
    """Progress trend → 中文进步解读 via tool-use agent."""
    tools = build_progress_tools(trend, comparison)
    user_payload = _serialize_progress(trend, comparison)
    out = run_agent_loop(
        backend, PROGRESS_SYSTEM_PROMPT, user_payload, tools,
        max_turns=max_turns, max_tokens=max_tokens,
    )
    return out["narration"]


def narrate_plan(
    plan: TrainingPlan,
    backend: ToolUseBackend,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Optional[str]:
    """TrainingPlan → 中文计划讲解 via tool-use agent."""
    tools = build_plan_tools(plan)
    user_payload = _serialize_plan(plan)
    out = run_agent_loop(
        backend, PLAN_SYSTEM_PROMPT, user_payload, tools,
        max_turns=max_turns, max_tokens=max_tokens,
    )
    return out["narration"]


def chat_with_coach(
    diagnosis: CoachDiagnosis,
    messages: List[ChatMessage],
    backend: ToolUseBackend,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Optional[str]:
    """多轮对话入口。复用 ``run_agent_loop`` + 诊断 tool 集。

    把 diagnosis 序列化进 system prompt（profile + issues + comparison +
    meta），把 chat history 转 OpenAI 风格 [{role, content}] 喂给
    ``run_agent_loop(seed_messages=...)``。返回 assistant 最终回复文本；
    失败（max_turns / 异常）返回 None（caller 决定降级文案）。
    """
    tools = build_diagnosis_tools(diagnosis)
    system = (
        CHAT_SYSTEM_PROMPT
        + "\n\n【本次会话诊断 payload（ground truth，禁止编造数值）】：\n"
        + _serialize_diagnosis(diagnosis)
    )
    seed = [
        {"role": m.role, "content": m.content}
        for m in messages
        if m.content  # 空消息跳过，防 backend 拒收
    ]
    if not seed:
        return None
    out = run_agent_loop(
        backend, system, "", tools,
        max_turns=max_turns, max_tokens=max_tokens,
        seed_messages=seed,
    )
    return out["narration"]


__all__ = [
    "run_agent_loop",
    "narrate_diagnosis",
    "narrate_progress",
    "narrate_plan",
    "chat_with_coach",
    "ChatMessage",
    "DIAGNOSIS_SYSTEM_PROMPT",
    "PROGRESS_SYSTEM_PROMPT",
    "PLAN_SYSTEM_PROMPT",
    "CHAT_SYSTEM_PROMPT",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MAX_TOKENS",
]
