"""Coach agent tools: bounded schema definitions + handler functions.

每个 tool = 一个 OpenAI function schema + 一个 Python handler。数据型 tool
(diagnosis / meta) 用闭包绑定本次会话 context；知识型 tool 调
:mod:`agent_kb` / :mod:`knowledge` 按需取预备切片。

设计参考 docs/superpowers/specs/2026-07-05-aiming-coach-agent-design.md §3。
所有 tool 只返回**预备好的片段**（不返回 LLM 生成内容）——防幻觉铁律落到
tool 层。
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Optional

from .agent_kb import BY_SIGNAL, BY_TOPIC, KB
from .diagnosis import CoachDiagnosis
from .knowledge import KNOWLEDGE
from .planning import TrainingPlan

# ---------------------------------------------------------------------------
# Tool schema builders — OpenAI function-calling form (also understood by
# DeepSeek-V3's OpenAI-compatible endpoint).
# ---------------------------------------------------------------------------


def _schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


_NO_PARAMS = {"type": "object", "properties": {}, "required": []}


def schema_get_diagnosis() -> dict[str, Any]:
    return _schema(
        "coach_get_diagnosis",
        "取本次诊断的完整 JSON payload（profile + issues + comparison + meta）。"
        "诊断是 ground truth，不可修改——本 tool 仅用于回查字段。",
        _NO_PARAMS,
    )


def schema_get_meta() -> dict[str, Any]:
    return _schema(
        "coach_get_meta",
        "取本次分析的 meta 信息（cm_per_360 / fps / 参考来源 / 录制时间等）。",
        _NO_PARAMS,
    )


def schema_list_signals() -> dict[str, Any]:
    return _schema(
        "coach_list_signals",
        "列出本次诊断里出现、且可调 coach_fetch_knowledge 检索社区知识的 signal key。",
        _NO_PARAMS,
    )


def schema_list_knowledge_topics() -> dict[str, Any]:
    return _schema(
        "coach_list_knowledge_topics",
        "列出可调 coach_fetch_kinematics / coach_fetch_prescription / "
        "coach_fetch_coaching_theory / coach_fetch_community_example 的 topic key。",
        _NO_PARAMS,
    )


def schema_fetch_knowledge() -> dict[str, Any]:
    return _schema(
        "coach_fetch_knowledge",
        "按 signal 取社区归因 + 可操作提示（来自 knowledge.py）。讲解每条 issue 前"
        "建议先调一次拿社区措辞。",
        {
            "type": "object",
            "properties": {
                "signal": {
                    "type": "string",
                    "description": "Signal key，必须用 coach_list_signals 返回的 key 之一。",
                },
            },
            "required": ["signal"],
        },
    )


def _topic_schema(name: str, description: str) -> dict[str, Any]:
    return _schema(
        name,
        description,
        {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic key，必须用 coach_list_knowledge_topics 返回的 key 之一。",
                },
            },
            "required": ["topic"],
        },
    )


def schema_fetch_kinematics() -> dict[str, Any]:
    return _topic_schema(
        "coach_fetch_kinematics",
        "取运动学理论章节片段（aim-kinematics-research.md：阈值 / min-jerk vs 匀减速 / "
        "SPARC / submovement / Fitts / sensitivity / scenarios 等）。",
    )


def schema_fetch_prescription() -> dict[str, Any]:
    return _topic_schema(
        "coach_fetch_prescription",
        "取处方手册章节片段（coach-prescription-manual.md：制动代价 / 子动作分类 / "
        "外部焦点 / 交错编排 / 元认知对抗 / 反馈褪除等）。",
    )


def schema_fetch_coaching_theory() -> dict[str, Any]:
    return _topic_schema(
        "coach_fetch_coaching_theory",
        "取教练理论章节片段（coach-theory-foundation.md：Fitts&Posner 三阶段 / "
        "刻意练习 / 情境干扰 / KR-KP / guidance hypothesis / Socratic 等）。",
    )


def schema_fetch_community_example() -> dict[str, Any]:
    return _topic_schema(
        "coach_fetch_community_example",
        "取社区前沿 + YouTube 创作者经验片段（Voltaic S5 / static clicking 三步 / "
        "bardOZ 方法 / 张力预算 / 复盘方法论等）。信源等级 = 社区共识 / 个人经验。",
    )


# Progress / plan context tools (Phase 2.7) ---------------------------------


def schema_get_trend() -> dict[str, Any]:
    return _schema(
        "coach_get_trend",
        "取本次进步报告的趋势数据（各指标的历史时间序列）。",
        _NO_PARAMS,
    )


def schema_get_comparison() -> dict[str, Any]:
    return _schema(
        "coach_get_comparison",
        "取本次进步报告的对比表（current / baseline / last / ref / verdict）。",
        _NO_PARAMS,
    )


def schema_get_plan() -> dict[str, Any]:
    return _schema(
        "coach_get_plan",
        "取训练计划结构（focus_metrics / adjustments / schedule_note / "
        "evidence_anchors）。",
        _NO_PARAMS,
    )


# ---------------------------------------------------------------------------
# Handler factories — bind session context, return a plain callable.
# Each handler returns a JSON-serializable dict (str on error path).
# ---------------------------------------------------------------------------


def _to_jsonable(obj: Any) -> Any:
    """Best-effort recursive dataclass -> dict so json.dumps works."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def _diag_payload(diagnosis: CoachDiagnosis) -> dict[str, Any]:
    return {
        "profile": _to_jsonable(diagnosis.profile),
        "issues": [
            {
                "priority": i.priority, "signal": i.signal, "severity": i.severity,
                "priority_reason": i.priority_reason,
                "root_causes": [{"level": rc.level, "text": rc.text}
                                for rc in i.root_causes],
                "prescriptions": [{"scenario": p.scenario, "reason": p.reason}
                                  for p in i.prescriptions],
            }
            for i in diagnosis.issues
        ],
        "comparison": diagnosis.comparison or [],
        "meta": diagnosis.meta or {},
    }


def make_get_diagnosis(diagnosis: CoachDiagnosis) -> Callable[[], dict[str, Any]]:
    def _handler() -> dict[str, Any]:
        return _diag_payload(diagnosis)
    return _handler


def make_get_meta(diagnosis: CoachDiagnosis) -> Callable[[], dict[str, Any]]:
    def _handler() -> dict[str, Any]:
        meta = diagnosis.meta or {}
        return {
            "cm_per_360": meta.get("cm_per_360"),
            "fps": meta.get("fps"),
            "reference_label": meta.get("reference_label"),
            "recorded_at": meta.get("recorded_at") or meta.get("timestamp"),
        }
    return _handler


def make_list_signals(diagnosis: CoachDiagnosis) -> Callable[[], dict[str, Any]]:
    def _handler() -> dict[str, Any]:
        sigs = sorted({i.signal for i in diagnosis.issues if i.signal})
        # 同时报告 knowledge.py 里可查的 signal，便于 LLM 判断 fetch_knowledge 是否能命中
        known = sorted(set(KNOWLEDGE.keys()))
        return {"signals_in_diagnosis": sigs, "knowledge_known_signals": known}
    return _handler


def make_list_knowledge_topics() -> Callable[[], dict[str, Any]]:
    def _handler() -> dict[str, Any]:
        # 按 fetch_* 分桶给出 topic key（避免 LLM 瞎猜导致反复失败）
        kinematics = sorted({
            c["topic"] for c in KB
            if c["source_ref"].startswith("aim-kinematics-research.md")
        })
        prescription = sorted({
            c["topic"] for c in KB
            if c["source_ref"].startswith("coach-prescription-manual.md")
        })
        theory = sorted({
            c["topic"] for c in KB
            if c["source_ref"].startswith("coach-theory-foundation.md")
        })
        community = sorted({
            c["topic"] for c in KB
            if c["source_ref"].startswith(("coach-community-frontier.md",
                                           "YouTube"))
        })
        return {
            "kinematics_topics": kinematics,
            "prescription_topics": prescription,
            "coaching_theory_topics": theory,
            "community_topics": community,
        }
    return _handler


def make_fetch_knowledge() -> Callable[[str], dict[str, Any]]:
    def _handler(signal: str) -> dict[str, Any]:
        k = KNOWLEDGE.get(signal)
        if not k:
            return {
                "error": "unknown signal",
                "valid_signals": sorted(KNOWLEDGE.keys()),
            }
        return {"signal": signal, "community": k.get("community", ""),
                "cues": k.get("cues", [])}
    return _handler


def _make_fetch_by_source(prefixes: tuple[str, ...], tool_name: str) -> Callable[[str], dict[str, Any]]:
    def _handler(topic: str) -> dict[str, Any]:
        chunks = [c for c in BY_TOPIC.get(topic, []) if c["source_ref"].startswith(prefixes)]
        if not chunks:
            valid = sorted({c["topic"] for c in KB if c["source_ref"].startswith(prefixes)})
            return {"error": "unknown topic", "tool": tool_name, "valid_topics": valid}
        c = chunks[0]
        return {
            "topic": c["topic"],
            "content": c["text"],
            "source_ref": c["source_ref"],
            "source_level": c["source_level"],
        }
    return _handler


def make_fetch_kinematics() -> Callable[[str], dict[str, Any]]:
    return _make_fetch_by_source(("aim-kinematics-research.md",), "coach_fetch_kinematics")


def make_fetch_prescription() -> Callable[[str], dict[str, Any]]:
    return _make_fetch_by_source(("coach-prescription-manual.md",), "coach_fetch_prescription")


def make_fetch_coaching_theory() -> Callable[[str], dict[str, Any]]:
    return _make_fetch_by_source(("coach-theory-foundation.md",), "coach_fetch_coaching_theory")


def make_fetch_community_example() -> Callable[[str], dict[str, Any]]:
    return _make_fetch_by_source(
        ("coach-community-frontier.md", "YouTube"), "coach_fetch_community_example")


# Progress / plan context binders -------------------------------------------


def make_get_trend(trend: dict, comparison: list[dict]) -> Callable[[], dict[str, Any]]:
    def _handler() -> dict[str, Any]:
        # trend 的值是 (timestamp, value) tuple 列表——JSON 化为 list
        serializable = {
            k: [list(point) for point in series]
            for k, series in trend.items()
        }
        return {"trend": serializable}
    return _handler


def make_get_comparison(trend: dict, comparison: list[dict]) -> Callable[[], dict[str, Any]]:
    def _handler() -> dict[str, Any]:
        return {"comparison": comparison or []}
    return _handler


def make_get_plan(plan: TrainingPlan) -> Callable[[], dict[str, Any]]:
    def _handler() -> dict[str, Any]:
        return {
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
    return _handler


# ---------------------------------------------------------------------------
# Bounded tool bundle: (schema, handler) pairs keyed by name.
# ---------------------------------------------------------------------------


class ToolBundle:
    """Pairs each tool name with its OpenAI schema + bound handler.

    Agent loop dispatches by name; ``ToolBundle.schema_list()`` is fed
    straight into ``backend.messages_create(tools=...)``.
    """

    def __init__(self) -> None:
        self._items: list[tuple[dict[str, Any], Callable[..., dict[str, Any]]]] = []

    def add(self, schema: dict[str, Any], handler: Callable[..., dict[str, Any]]) -> None:
        self._items.append((schema, handler))

    def schema_list(self) -> list[dict[str, Any]]:
        return [s for s, _ in self._items]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        for schema, handler in self._items:
            if schema["function"]["name"] == name:
                try:
                    return handler(**arguments)
                except TypeError as e:
                    return {"error": "bad arguments", "detail": str(e),
                            "schema": schema["function"]["parameters"]}
        return {"error": "unknown tool", "name": name,
                "valid_tools": [s["function"]["name"] for s, _ in self._items]}

    def names(self) -> list[str]:
        return [s["function"]["name"] for s, _ in self._items]


def build_diagnosis_tools(diagnosis: CoachDiagnosis) -> ToolBundle:
    """Tool set for ``narrate_diagnosis`` (full knowledge access)."""
    b = ToolBundle()
    b.add(schema_get_diagnosis(), make_get_diagnosis(diagnosis))
    b.add(schema_get_meta(), make_get_meta(diagnosis))
    b.add(schema_list_signals(), make_list_signals(diagnosis))
    b.add(schema_list_knowledge_topics(), make_list_knowledge_topics())
    b.add(schema_fetch_knowledge(), make_fetch_knowledge())
    b.add(schema_fetch_kinematics(), make_fetch_kinematics())
    b.add(schema_fetch_prescription(), make_fetch_prescription())
    b.add(schema_fetch_coaching_theory(), make_fetch_coaching_theory())
    b.add(schema_fetch_community_example(), make_fetch_community_example())
    return b


def build_progress_tools(trend: dict, comparison: list[dict]) -> ToolBundle:
    """Tool set for ``narrate_progress`` (trend/comparison + knowledge)."""
    b = ToolBundle()
    b.add(schema_get_trend(), make_get_trend(trend, comparison))
    b.add(schema_get_comparison(), make_get_comparison(trend, comparison))
    b.add(schema_list_knowledge_topics(), make_list_knowledge_topics())
    b.add(schema_fetch_kinematics(), make_fetch_kinematics())
    b.add(schema_fetch_prescription(), make_fetch_prescription())
    b.add(schema_fetch_coaching_theory(), make_fetch_coaching_theory())
    b.add(schema_fetch_community_example(), make_fetch_community_example())
    return b


def build_plan_tools(plan: TrainingPlan) -> ToolBundle:
    """Tool set for ``narrate_plan`` (plan data + theory/prescription KB)."""
    b = ToolBundle()
    b.add(schema_get_plan(), make_get_plan(plan))
    b.add(schema_list_knowledge_topics(), make_list_knowledge_topics())
    b.add(schema_fetch_kinematics(), make_fetch_kinematics())
    b.add(schema_fetch_prescription(), make_fetch_prescription())
    b.add(schema_fetch_coaching_theory(), make_fetch_coaching_theory())
    b.add(schema_fetch_community_example(), make_fetch_community_example())
    return b


__all__ = [
    "ToolBundle",
    "build_diagnosis_tools",
    "build_progress_tools",
    "build_plan_tools",
]
