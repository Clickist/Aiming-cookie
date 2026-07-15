"""Tests for the coach agent loop (mocked ToolUseBackend — no real LLM calls)."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from kovaak_tracker.coach.agent import (
    run_agent_loop,
    narrate_diagnosis,
    narrate_progress,
    narrate_plan,
    DIAGNOSIS_SYSTEM_PROMPT,
    DEFAULT_MAX_TURNS,
)
from kovaak_tracker.advice import Prescription
from kovaak_tracker.coach.agent_tools import build_diagnosis_tools, diagnosis_payload
from kovaak_tracker.coach.diagnosis import (
    CoachDiagnosis, DiagnosisIssue, ProfileMatch, RootCause,
)
from kovaak_tracker.coach.planning import PlanAdjustment, TrainingPlan
from kovaak_tracker.coach.providers import ToolUseResponse


# ---------------------------------------------------------------------------
# Mock backend: scripted sequence of ToolUseResponse.
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


def _contract_diag() -> CoachDiagnosis:
    return CoachDiagnosis(
        profile=ProfileMatch("decel_jitter", "减速抖动观察型", 0.8, []),
        issues=[DiagnosisIssue(
            signal="sparc low",
            severity="info",
            priority=1,
            priority_reason="[experimental] 观察项排序第 1",
            root_causes=[RootCause("symptom", "减速轮廓存在较多快速波动")],
            prescriptions=[Prescription(
                scenario="pasu",
                reason="练习连续减速",
                cue="接近目标时让速度连续下降，不要硬停",
                purpose="减少减速末段的速度波动",
                target_metrics=["sparc", "reverse_ratio"],
                expected_direction=["sparc ↑", "reverse_ratio ↓"],
                retest_after="同场景完成一组后复测",
                stop_or_adjust_rule="若准确率明显下降，降低速度或放大目标",
                source_level="community_consensus",
            )],
            plain_language_meaning="减速过程不够连续",
            claim_level="experimental",
            metric_refs=["sparc", "reverse_ratio"],
            event_refs=["flick:37"],
            limitations=["threshold_requires_product_calibration"],
            expected_result="减速更连续，反向修正减少",
            verification={
                "comparable_requirements": ["相同场景", "相同设置"],
                "success_signals": ["sparc ↑", "reverse_ratio ↓"],
                "insufficient_evidence_behavior": "样本不足时只记录",
                "raw_payload": {"dx": [123456]},
                "api_key": "sk-secret-sentinel",
                "source_path": "/Users/clickist/private.trace",
            },
        )],
        summary={"raw_dx": [123456]},
        comparison=[{
            "metric": "sparc",
            "status": "below_reference",
            "reason": "sk-secret-in-allowed-field",
            "api_key": "sk-secret-sentinel",
            "source_path": "/Users/clickist/private.trace",
        }],
        meta={
            "cm_per_360": 48.0,
            "fps": 240.0,
            "reference_label": "self baseline",
            "analysis_context": {"raw_dx": [123456]},
            "classification": "/Users/clickist/private.trace",
            "raw_payload": {"dx": [123456]},
            "api_key": "sk-secret-sentinel",
            "source_path": "/Users/clickist/private.trace",
        },
    )


def _assert_explanation_contract(payload: dict[str, Any]) -> None:
    issue = payload["issues"][0]
    assert issue["plain_language_meaning"] == "减速过程不够连续"
    assert issue["claim_level"] == "experimental"
    assert issue["metric_refs"] == ["sparc", "reverse_ratio"]
    assert issue["event_refs"] == ["flick:37"]
    assert issue["limitations"] == ["threshold_requires_product_calibration"]
    assert issue["expected_result"] == "减速更连续，反向修正减少"
    assert issue["verification"] == {
        "comparable_requirements": ["相同场景", "相同设置"],
        "success_signals": ["sparc ↑", "reverse_ratio ↓"],
        "insufficient_evidence_behavior": "样本不足时只记录",
    }

    prescription = issue["prescriptions"][0]
    assert prescription == {
        "scenario": "pasu",
        "reason": "练习连续减速",
        "cue": "接近目标时让速度连续下降，不要硬停",
        "purpose": "减少减速末段的速度波动",
        "target_metrics": ["sparc", "reverse_ratio"],
        "expected_direction": ["sparc ↑", "reverse_ratio ↓"],
        "retest_after": "同场景完成一组后复测",
        "stop_or_adjust_rule": "若准确率明显下降，降低速度或放大目标",
        "source_level": "community_consensus",
    }

    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "raw_payload", "raw_dx", "123456", "api_key",
        "sk-secret-sentinel", "sk-secret-in-allowed-field",
        "source_path", "/Users/clickist/private.trace",
    ):
        assert forbidden not in serialized


# ---------------------------------------------------------------------------
# Test 1: 1 tool call → result fed back → end_turn with narration
# ---------------------------------------------------------------------------


def test_one_tool_call_then_end():
    diag = _diag()
    tools = build_diagnosis_tools(diag)
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": "t1", "name": "coach_get_meta"}]),
        _end_resp("讲解：你的减速段抖动需要修。"),
    ])
    out = run_agent_loop(
        backend, DIAGNOSIS_SYSTEM_PROMPT,
        json.dumps({"profile": {}}, ensure_ascii=False), tools,
        max_turns=4,
    )
    assert out["narration"] == "讲解：你的减速段抖动需要修。"
    assert out["stop_reason"] == "end_turn"
    assert len(out["trace"]) == 1
    assert out["trace"][0]["tool"] == "coach_get_meta"
    # 第二次调用收到的 messages 应含 tool_result block
    second_messages = backend.calls[1]["messages"]
    last_user = second_messages[-1]
    assert last_user["role"] == "user"
    tool_results = [b for b in last_user["content"] if b.get("type") == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0]["tool_use_id"] == "t1"
    # assistant 中间帧带 tool_use block（id 对应）
    asst = second_messages[-2]
    assert asst["role"] == "assistant"
    asst_tool_uses = [b for b in asst["content"] if b.get("type") == "tool_use"]
    assert len(asst_tool_uses) == 1
    assert asst_tool_uses[0]["id"] == "t1"


def test_narrate_diagnosis_payload_preserves_safe_explanation_contract():
    backend = _ScriptedBackend([_end_resp("讲解完成。")])

    out = narrate_diagnosis(_contract_diag(), backend, max_turns=1)

    assert out == "讲解完成。"
    payload = json.loads(backend.calls[0]["messages"][0]["content"])
    _assert_explanation_contract(payload)


def test_get_diagnosis_tool_preserves_safe_explanation_contract():
    tools = build_diagnosis_tools(_contract_diag())

    payload = tools.dispatch("coach_get_diagnosis", {})

    _assert_explanation_contract(payload)


def test_python_sink_filters_sensitive_explanation_fields_and_fails_closed():
    base = _contract_diag()
    issue = replace(
        base.issues[0],
        plain_language_meaning="access_token=access-token-secret-sentinel",
        claim_level="unknown_weak_level",
        expected_result="../private-result.json",
        root_causes=[RootCause("physical", "Bearer sk-live-secret-sentinel")],
        prescriptions=[Prescription(
            scenario="~/private-scenario.json",
            reason="api_key=api-key-secret-sentinel",
            cue="file:///private/cue.txt",
            purpose="refresh_token=refresh-token-secret-sentinel",
            target_metrics=["sparc", "../private-metric.json"],
            expected_direction=["sparc ↑", "Bearer sk-direction-secret"],
            retest_after="/private/retest.txt",
            stop_or_adjust_rule="secret=stop-secret-sentinel",
            source_level="unknown_weak_level",
        )],
    )
    diagnosis = replace(
        base,
        profile=ProfileMatch(
            "decel_jitter",
            "api_key=profile-secret-sentinel",
            0.8,
            ["safe-tag", "../private-tag.json"],
        ),
        issues=[issue],
    )

    payload = diagnosis_payload(diagnosis)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["issues"][0]["claim_level"] == "experimental"
    assert payload["issues"][0]["prescriptions"][0]["source_level"] == "experimental"
    assert payload["profile"]["secondary_tags"] == ["safe-tag"]
    for forbidden in (
        "access-token-secret-sentinel",
        "sk-live-secret-sentinel",
        "api-key-secret-sentinel",
        "refresh-token-secret-sentinel",
        "stop-secret-sentinel",
        "profile-secret-sentinel",
        "private-result.json",
        "private-scenario.json",
        "private-metric.json",
        "private-tag.json",
    ):
        assert forbidden not in serialized


def test_narrate_diagnosis_end_to_end_mock():
    diag = _diag()
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": "a", "name": "coach_fetch_knowledge",
                          "arguments": {"signal": "sparc low"}}]),
        _end_resp("你属于减速抖动型，建议练 pasu。"),
    ])
    out = narrate_diagnosis(diag, backend, max_turns=4)
    assert out == "你属于减速抖动型，建议练 pasu。"
    # fetch_knowledge 实际命中 knowledge.py 的真实数据
    tool_result_content = backend.calls[1]["messages"][-1]["content"][0]["content"]
    parsed = json.loads(tool_result_content)
    assert parsed["signal"] == "sparc low"
    assert "MattyOW" in parsed["community"] or "张力" in parsed["community"]


# ---------------------------------------------------------------------------
# Test 2: agent keeps calling tools → max_turns exceeded → degrades to None
# ---------------------------------------------------------------------------


def test_max_turns_exhaustion():
    diag = _diag()
    tools = build_diagnosis_tools(diag)
    # 后端永远要 tool，永远不 end_turn
    script = [
        _tool_call_resp([{"id": f"t{i}", "name": "coach_get_meta"}])
        for i in range(DEFAULT_MAX_TURNS + 5)
    ]
    backend = _ScriptedBackend(script)
    out = run_agent_loop(
        backend, DIAGNOSIS_SYSTEM_PROMPT,
        json.dumps({}), tools, max_turns=3,
    )
    assert out["narration"] is None
    assert out["stop_reason"] == "max_turns_exceeded"
    assert "did not converge" in out["error"]
    # 只应被调 max_turns=3 次（不是 DEFAULT）
    assert len(backend.calls) == 3


def test_max_turns_hard_cap():
    diag = _diag()
    tools = build_diagnosis_tools(diag)
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": f"t{i}", "name": "coach_get_meta"}])
        for i in range(1000)
    ])
    out = run_agent_loop(
        backend, "sys", "{}", tools, max_turns=10_000,  # 应被 cap 到 12
    )
    assert out["stop_reason"] == "max_turns_exceeded"
    assert len(out["trace"]) == 12  # MAX_TURNS_HARD_CAP


# ---------------------------------------------------------------------------
# Test 3: unknown tool key → valid_keys 反馈给 LLM
# ---------------------------------------------------------------------------


def test_unknown_signal_returns_valid_keys():
    diag = _diag()
    tools = build_diagnosis_tools(diag)
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": "x", "name": "coach_fetch_knowledge",
                          "arguments": {"signal": "nope nope"}}]),
        _end_resp("ok 降级讲解。"),
    ])
    out = run_agent_loop(backend, "sys", "{}", tools, max_turns=4)
    # tool 失败但 loop 继续——最终 narration 来自第二轮
    assert out["narration"] == "ok 降级讲解。"
    fed_back = backend.calls[1]["messages"][-1]["content"][0]["content"]
    parsed = json.loads(fed_back)
    assert parsed["error"] == "unknown signal"
    assert "sparc low" in parsed["valid_signals"]


def test_unknown_topic_returns_valid_topics():
    diag = _diag()
    tools = build_diagnosis_tools(diag)
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": "y", "name": "coach_fetch_kinematics",
                          "arguments": {"topic": "made_up_topic"}}]),
        _end_resp("讲解略过该理论。"),
    ])
    out = run_agent_loop(backend, "sys", "{}", tools, max_turns=4)
    assert out["narration"] == "讲解略过该理论。"
    fed_back = json.loads(backend.calls[1]["messages"][-1]["content"][0]["content"])
    assert fed_back["error"] == "unknown topic"
    assert fed_back["tool"] == "coach_fetch_kinematics"
    assert "sparc" in fed_back["valid_topics"]  # 真实 KB 里的 key


def test_unknown_tool_name_returns_valid_tools():
    diag = _diag()
    tools = build_diagnosis_tools(diag)
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": "z", "name": "coach_does_not_exist", "arguments": {}}]),
        _end_resp("讲解。"),
    ])
    out = run_agent_loop(backend, "sys", "{}", tools, max_turns=4)
    fed_back = json.loads(backend.calls[1]["messages"][-1]["content"][0]["content"])
    assert fed_back["error"] == "unknown tool"
    assert "coach_get_diagnosis" in fed_back["valid_tools"]


# ---------------------------------------------------------------------------
# Test 4: exception in backend → graceful None
# ---------------------------------------------------------------------------


class _BoomBackend:
    def messages_create(self, *, system, messages, tools, max_tokens=2048):
        raise RuntimeError("network down")


def test_backend_exception_degrades_to_none():
    diag = _diag()
    tools = build_diagnosis_tools(diag)
    out = run_agent_loop(_BoomBackend(), "sys", "{}", tools, max_turns=4)
    assert out["narration"] is None
    assert out["stop_reason"] == "exception"
    assert "network down" in out["error"]


# ---------------------------------------------------------------------------
# Test 5: narrate_progress / narrate_plan with mock backend
# ---------------------------------------------------------------------------


def test_narrate_progress_mock():
    trend = {"sparc": [("t1", -7.0), ("t2", -5.0)]}
    comparison = [{"metric": "sparc", "current": -5.0, "baseline": -7.0,
                   "verdict": "better"}]
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": "p1", "name": "coach_get_trend"}]),
        _end_resp("你的 SPARC 进步了，下阶段练 pasu。"),
    ])
    out = narrate_progress(trend, comparison, backend, max_turns=4)
    assert out == "你的 SPARC 进步了，下阶段练 pasu。"


def test_narrate_plan_mock():
    plan = TrainingPlan(
        focus_metrics=["sparc"],
        adjustments=[PlanAdjustment(
            kind="interleave", target_metric="sparc", scenarios=[],
            reason="交错更好", evidence="§4.2",
        )],
        schedule_note="每周复测",
        evidence_anchors=["§4.2"],
        notes=[],
    )
    backend = _ScriptedBackend([
        _tool_call_resp([{"id": "pl1", "name": "coach_get_plan"}]),
        _end_resp("下阶段交错 pasu 和 multiclick。"),
    ])
    out = narrate_plan(plan, backend, max_turns=4)
    assert out == "下阶段交错 pasu 和 multiclick。"


# ---------------------------------------------------------------------------
# Test 6: max_turns / max_tokens 丢弃半截 preamble(spec §8 regression)
# ---------------------------------------------------------------------------


def test_max_turns_exhaustion_with_partial_text():
    """max_turns 耗尽时,即使模型产了半截 preamble("让我查一下..."),
    narration 也应为 None——半截文本不能当成功讲解。

    回归:修复前 last_text 被当 narration 返回,report.py 不触发降级,
    用户看到前导词当最终讲解(违反 spec §8)。
    """
    diag = _diag()
    tools = build_diagnosis_tools(diag)
    script = [
        _tool_call_resp(
            [{"id": f"t{i}", "name": "coach_get_meta"}],
            text=f"让我查一下第 {i+1} 个数据",
        )
        for i in range(DEFAULT_MAX_TURNS + 5)
    ]
    backend = _ScriptedBackend(script)
    out = run_agent_loop(
        backend, DIAGNOSIS_SYSTEM_PROMPT,
        json.dumps({}), tools, max_turns=3,
    )
    assert out["narration"] is None
    assert out["stop_reason"] == "max_turns_exceeded"


def test_max_tokens_truncation_discards_narration():
    """max_tokens 截断的文本可能不完整,narration 应为 None 触发降级。"""
    diag = _diag()
    tools = build_diagnosis_tools(diag)
    backend = _ScriptedBackend([
        _end_resp("这是一段被截断的半", stop="max_tokens"),
    ])
    out = run_agent_loop(backend, "sys", "{}", tools, max_turns=4)
    assert out["narration"] is None
    assert out["stop_reason"] == "max_tokens"
