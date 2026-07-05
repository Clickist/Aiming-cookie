"""Tests for chat_with_coach (mocked ToolUseBackend — no real LLM calls)."""
from __future__ import annotations

import json
from typing import Any

from kovaak_tracker.coach.agent import (
    CHAT_SYSTEM_PROMPT,
    ChatMessage,
    chat_with_coach,
)
from kovaak_tracker.coach.diagnosis import (
    CoachDiagnosis, DiagnosisIssue, ProfileMatch, RootCause,
)
from kovaak_tracker.coach.providers import ToolUseResponse


# ---------------------------------------------------------------------------
# Reusable mock backend (scripted sequence of ToolUseResponse)
# ---------------------------------------------------------------------------


class _ScriptedBackend:
    """Returns canned ToolUseResponse in order; raises if exhausted."""

    def __init__(self, script: list[ToolUseResponse]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def messages_create(self, *, system, messages, tools, max_tokens=2048):
        self.calls.append({
            "system": system, "messages": messages,
            "tools": tools, "max_tokens": max_tokens,
        })
        if not self.script:
            raise AssertionError("script exhausted — backend called too many times")
        return self.script.pop(0)


def _tool_call_resp(calls: list[dict[str, Any]], text: str = "") -> ToolUseResponse:
    return ToolUseResponse(
        content_text=text,
        tool_calls=[{"id": c["id"], "name": c["name"], "arguments": c.get("arguments", {})}
                    for c in calls],
        stop_reason="tool_calls",
    )


def _end_resp(text: str, stop: str = "end_turn") -> ToolUseResponse:
    return ToolUseResponse(content_text=text, tool_calls=[], stop_reason=stop)


def _diag() -> CoachDiagnosis:
    return CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动型", 1.0, []),
        issues=[DiagnosisIssue(
            signal="sparc low", severity="fix", priority=1,
            priority_reason="top",
            root_causes=[RootCause("symptom", "减速段抖动")],
            prescriptions=[],
        )],
        summary={"sparc": {"med": -7.0}},
        comparison=None,
        meta={"cm_per_360": 48.0},
    )


# ---------------------------------------------------------------------------
# Test 1: user 问术语 → coach 调 fetch_kinematics → 回答;然后追问测 history
# ---------------------------------------------------------------------------


def test_chat_single_turn_with_tool():
    diag = _diag()
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": "k1", "name": "coach_fetch_kinematics",
                          "arguments": {"topic": "sparc"}}]),
        _end_resp("SPARC 是衡量减速平滑度的指标。你的 -7.0 偏低。"),
    ])
    reply = chat_with_coach(
        diag,
        [ChatMessage(role="user", content="SPARC 是什么意思?")],
        backend,
        max_turns=4,
    )
    assert reply == "SPARC 是衡量减速平滑度的指标。你的 -7.0 偏低。"
    # system prompt 含 CHAT 前缀 + 诊断 payload
    sys_prompt = backend.calls[0]["system"]
    assert sys_prompt.startswith(CHAT_SYSTEM_PROMPT)
    assert "decel_jitter" in sys_prompt  # profile 已注入
    # seed_messages 含历史 user message
    msgs = backend.calls[0]["messages"]
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "SPARC 是什么意思?"


def test_chat_multi_turn_history_forwarded():
    """第 1 轮 user 问 → assistant 答;第 2 轮 user 追问,history 必须传到 backend。"""
    diag = _diag()
    backend = _ScriptedBackend([
        # 第 1 轮:问术语 → 拉定义 → 答
        _tool_call_resp([{"id": "k1", "name": "coach_fetch_kinematics",
                          "arguments": {"topic": "sparc"}}]),
        _end_resp("SPARC 是减速平滑度指标。"),
        # 第 2 轮:追问 pasu 训练 → 答(无需 tool)
        _end_resp("pasu 适合练你的减速段,每天 10 分钟。"),
    ])
    history = [
        ChatMessage(role="user", content="SPARC 是什么?"),
        ChatMessage(role="assistant", content="SPARC 是减速平滑度指标。"),
    ]
    reply1 = chat_with_coach(diag, history[:1], backend, max_turns=4)
    assert reply1 == "SPARC 是减速平滑度指标。"

    # 第 2 轮:传完整 history(2 条)+ 新 user 追问
    history.append(ChatMessage(role="user", content="那 pasu 怎么练?"))
    reply2 = chat_with_coach(diag, history, backend, max_turns=4)
    assert reply2 == "pasu 适合练你的减速段,每天 10 分钟。"

    # 验证第 2 次调用收到的 seed 含完整 3 条 history
    second_messages = backend.calls[2]["messages"]
    assert len(second_messages) == 3
    assert second_messages[0]["content"] == "SPARC 是什么?"
    assert second_messages[1]["content"] == "SPARC 是减速平滑度指标。"
    assert second_messages[2]["content"] == "那 pasu 怎么练?"


# ---------------------------------------------------------------------------
# Test 2: 未知术语 → coach 降级
# ---------------------------------------------------------------------------


def test_chat_unknown_topic_degrades_gracefully():
    diag = _diag()
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": "u1", "name": "coach_fetch_kinematics",
                          "arguments": {"topic": "made_up_thing"}}]),
        _end_resp("这个术语我没有把握,不敢乱说。"),
    ])
    reply = chat_with_coach(
        diag,
        [ChatMessage(role="user", content="什么是 XYZ 术语?")],
        backend, max_turns=4,
    )
    assert reply == "这个术语我没有把握,不敢乱说。"
    # tool 返回的 valid_topics 反馈进 messages
    fed = json.loads(backend.calls[1]["messages"][-1]["content"][0]["content"])
    assert fed["error"] == "unknown topic"


# ---------------------------------------------------------------------------
# Test 3: tool 失败 / backend 异常 → 返回 None
# ---------------------------------------------------------------------------


class _BoomBackend:
    def messages_create(self, *, system, messages, tools, max_tokens=2048):
        raise RuntimeError("network down")


def test_chat_backend_exception_returns_none():
    diag = _diag()
    reply = chat_with_coach(
        diag,
        [ChatMessage(role="user", content="hi")],
        _BoomBackend(), max_turns=4,
    )
    assert reply is None


def test_chat_empty_messages_returns_none():
    diag = _diag()
    backend = _ScriptedBackend([_end_resp("never reached")])
    reply = chat_with_coach(diag, [], backend, max_turns=4)
    assert reply is None
    assert len(backend.calls) == 0  # 没调 backend


# ---------------------------------------------------------------------------
# Test 4: max_turns 耗尽 → None
# ---------------------------------------------------------------------------


def test_chat_max_turns_exhaustion():
    diag = _diag()
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": f"t{i}", "name": "coach_get_meta"}])
        for i in range(20)
    ])
    reply = chat_with_coach(
        diag,
        [ChatMessage(role="user", content="问问问")],
        backend, max_turns=3,
    )
    assert reply is None
    assert len(backend.calls) == 3


# ---------------------------------------------------------------------------
# Test 5: 跳过空消息(防 backend 拒收)
# ---------------------------------------------------------------------------


def test_chat_skips_empty_messages():
    diag = _diag()
    backend = _ScriptedBackend([_end_resp("ok")])
    reply = chat_with_coach(
        diag,
        [
            ChatMessage(role="user", content=""),
            ChatMessage(role="user", content="真的有问题"),
            ChatMessage(role="assistant", content=""),
        ],
        backend, max_turns=4,
    )
    assert reply == "ok"
    msgs = backend.calls[0]["messages"]
    assert len(msgs) == 1
    assert msgs[0]["content"] == "真的有问题"
