"""Coach agent tools: bounded schema definitions + handler functions.

每个 tool = 一个 OpenAI function schema + 一个 Python handler。数据型 tool
(diagnosis / meta) 用闭包绑定本次会话 context；知识型 tool 调
:mod:`agent_kb` / :mod:`knowledge` 按需取预备切片。

设计参考 docs/superpowers/specs/2026-07-05-aiming-coach-agent-design.md §3。
所有 tool 只返回**预备好的片段**（不返回 LLM 生成内容）——防幻觉铁律落到
tool 层。
"""
from __future__ import annotations

import math
import os
import re
from typing import Any, Callable, Optional

from .agent_kb import BY_TOPIC, KB
from .diagnosis import CoachDiagnosis
from .knowledge_registry import claim_ref, entry_ref, load_registry, query_registry
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


_COMPARISON_FIELDS = (
    "metric", "current", "baseline", "last", "self", "ref", "delta",
    "verdict", "status", "reason", "comparable", "unit", "classification",
)
_META_FIELDS = (
    "summary_type", "cm_per_360", "fps", "ball_w", "reference_label",
    "recorded_at", "timestamp", "analysis_context", "metric_version",
    "scenario_identity_version", "calibration_compatibility",
    "minimum_evidence_quality", "classification",
)


_MISSING = object()
_CLAIM_LEVELS = frozenset(
    {"measured", "deterministic_rule", "research_supported", "community_consensus", "experimental"}
)
_SOURCE_LEVELS = frozenset(
    {"product_contract", "academic_peer_reviewed", "community_consensus", "personal_experience_unverified", "experimental"}
)


def _is_path_like(value: str) -> bool:
    return (
        os.path.isabs(value)
        or value.startswith(("file://", "~/", "../", "..\\", "\\"))
        or (
            len(value) >= 3
            and value[0].isalpha()
            and value[1] == ":"
            and value[2] in {"/", "\\"}
        )
    )


def _contains_sensitive_text(value: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"client[_-]?secret|password|secret)\s*[:=]|\bbearer\s+\S{8,}|"
            r"\b(?:sk-|ghp_|github_pat_)[a-z0-9_-]{8,}",
            value,
        )
    )


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _MISSING
    if not isinstance(value, str):
        return _MISSING
    if _is_path_like(value) or _contains_sensitive_text(value):
        return _MISSING
    return value


def _safe_text(value: Any) -> str:
    safe = _safe_scalar(value)
    return safe if isinstance(safe, str) else ""


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        safe
        for item in value
        if (safe := _safe_scalar(item)) is not _MISSING
        and isinstance(safe, str)
    ]


def _allowed_fields(data: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    result = {}
    for key in fields:
        if key not in data:
            continue
        value = _safe_scalar(data[key])
        if value is not _MISSING:
            result[key] = value
    return result


def _verification_payload(verification: Any) -> dict[str, Any]:
    if not isinstance(verification, dict):
        return {}
    result = {}
    for key in ("comparable_requirements", "success_signals"):
        values = _safe_string_list(verification.get(key))
        if values:
            result[key] = values
    insufficient = _safe_scalar(
        verification.get("insufficient_evidence_behavior")
    )
    if insufficient is not _MISSING:
        result["insufficient_evidence_behavior"] = insufficient
    return result


def diagnosis_payload(diagnosis: CoachDiagnosis) -> dict[str, Any]:
    """Serialize the safe Coach explanation contract for every Python sink."""
    return {
        "profile": {
            "archetype_id": _safe_text(diagnosis.profile.archetype_id),
            "label": _safe_text(diagnosis.profile.label),
            "confidence": diagnosis.profile.confidence,
            "secondary_tags": _safe_string_list(diagnosis.profile.secondary_tags),
        },
        "issues": [
            {
                "priority": issue.priority,
                "signal": _safe_text(issue.signal),
                "severity": _safe_text(issue.severity),
                "priority_reason": _safe_text(issue.priority_reason),
                "plain_language_meaning": _safe_text(issue.plain_language_meaning),
                "claim_level": (
                    issue.claim_level
                    if issue.claim_level in _CLAIM_LEVELS
                    else "experimental"
                ),
                "metric_refs": _safe_string_list(issue.metric_refs),
                "event_refs": _safe_string_list(issue.event_refs),
                "limitations": _safe_string_list(issue.limitations),
                "expected_result": _safe_text(issue.expected_result),
                "verification": _verification_payload(issue.verification),
                "root_causes": [
                    {
                        "level": _safe_text(cause.level) or "symptom",
                        "text": _safe_text(cause.text),
                    }
                    for cause in issue.root_causes
                    if _safe_text(cause.text)
                ],
                "prescriptions": [
                    {
                        "scenario": _safe_text(prescription.scenario),
                        "reason": _safe_text(prescription.reason),
                        "cue": _safe_text(prescription.cue),
                        "purpose": _safe_text(prescription.purpose),
                        "target_metrics": _safe_string_list(
                            prescription.target_metrics
                        ),
                        "expected_direction": _safe_string_list(
                            prescription.expected_direction
                        ),
                        "retest_after": _safe_text(prescription.retest_after),
                        "stop_or_adjust_rule": (
                            _safe_text(prescription.stop_or_adjust_rule)
                        ),
                        "source_level": (
                            prescription.source_level
                            if prescription.source_level in _SOURCE_LEVELS
                            else "experimental"
                        ),
                    }
                    for prescription in issue.prescriptions
                ],
            }
            for issue in diagnosis.issues
        ],
        "comparison": [
            _allowed_fields(row, _COMPARISON_FIELDS)
            for row in diagnosis.comparison or []
            if isinstance(row, dict)
        ],
        "meta": _allowed_fields(diagnosis.meta, _META_FIELDS),
    }


def make_get_diagnosis(diagnosis: CoachDiagnosis) -> Callable[[], dict[str, Any]]:
    def _handler() -> dict[str, Any]:
        return diagnosis_payload(diagnosis)
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
        # Keep legacy buckets while deriving every topic from the Registry.
        kinematics = _topics_for_kind("kinematics")
        prescription = _topics_for_kind("prescription")
        theory = _topics_for_kind("theory")
        community = _topics_for_kind("community")
        return {
            "kinematics_topics": kinematics,
            "prescription_topics": prescription,
            "coaching_theory_topics": theory,
            "community_topics": community,
        }
    return _handler


def make_fetch_knowledge() -> Callable[[str], dict[str, Any]]:
    def _handler(signal: str) -> dict[str, Any]:
        data = load_registry()
        canonical = data["signal_aliases"].get(signal, signal)
        selected = query_registry(data, issue_signal=canonical)
        if not selected:
            return {
                "error": "unknown signal",
                "valid_signals": sorted({
                    item
                    for entry in data["entries"]
                    if entry["status"] == "active"
                    for item in entry["signals"]
                }),
            }
        cues = [
            entry["cue"]["text"]
            for entry in selected
            if isinstance(entry.get("cue"), dict)
        ]
        return {
            "signal": canonical,
            "community": cues[0] if cues else "",
            "cues": cues,
            "registry_version": data["registry_version"],
            "entries": [_entry_payload(entry, data) for entry in selected],
        }
    return _handler


def _matches_kind(chunk: dict[str, Any], kind: str) -> bool:
    if kind == "kinematics":
        return chunk["category"] in {
            "metric_definition", "kinematic_mechanism", "diagnostic_scope",
            "limitation_counterevidence", "research",
        }
    if kind == "prescription":
        return chunk["category"] in {"training_cue", "prescription_verification"}
    if kind == "theory":
        return chunk["category"] in {"practice_structure", "research", "kinematic_mechanism"}
    return chunk["source_level"] in {
        "community_consensus", "personal_experience_unverified", "experimental",
    }


def _topics_for_kind(kind: str) -> list[str]:
    return sorted({topic for topic, chunks in BY_TOPIC.items() if any(
        _matches_kind(chunk, kind) for chunk in chunks
    )})


def _v2_sections(entry: dict[str, Any]) -> list[dict[str, Any]]:
    sections = [entry["definition"], entry["scope"], entry["expected_direction"]]
    sections.extend(entry["mechanisms"])
    for name in (
        "cue", "dose_guardrail", "matched_retest", "near_transfer_retest",
        "stop_adjust_rule",
    ):
        value = entry[name]
        if isinstance(value, dict):
            sections.append(value)
        elif isinstance(value, list):
            sections.extend(value)
    return sections


def _entry_payload(entry: dict[str, Any], registry_data: dict[str, Any]) -> dict[str, Any]:
    if registry_data["schema_version"] == "coach_knowledge_registry.v1":
        return {
            "entry_ref": entry_ref(entry),
            "entry_version": entry["entry_version"],
            "content": entry["text"],
            "sources": list(entry["sources"]),
            "max_claim_level": entry["max_claim_level"],
            "limitations": list(entry["limitations"]),
            "counterevidence": list(entry["counterevidence"]),
            "supported_uses": list(entry["supported_uses"]),
        }
    sections = _v2_sections(entry)
    sources_by_ref = {
        source["source_ref"]: source for source in registry_data["sources"]
    }
    claim_rank = {
        "experimental": 0,
        "community_practice": 1,
        "community_consensus": 2,
        "research_supported": 3,
        "deterministic_rule": 4,
    }
    max_claim_level = max(
        (section["claim_level"] for section in sections),
        key=claim_rank.__getitem__,
    )
    return {
        "entry_ref": entry_ref(entry),
        "entry_version": entry["entry_version"],
        "content": entry["definition"]["text"],
        "coaching_record": {
            "family_scope": list(entry["family_scope"]),
            "observation_refs": list(entry["observation_refs"]),
            "quality_prerequisites": list(entry["quality_prerequisites"]),
            "expected_direction": entry["expected_direction"]["text"],
            "alternative_explanations": list(entry["alternative_explanations"]),
            "forbidden_inferences": list(entry["forbidden_inferences"]),
            "cue": entry["cue"],
            "dose_guardrail": entry["dose_guardrail"],
            "matched_retest": entry["matched_retest"],
            "near_transfer_retest": entry["near_transfer_retest"],
            "stop_adjust_rule": entry["stop_adjust_rule"],
        },
        "sources": [sources_by_ref[source_ref] for source_ref in entry["sources"]],
        "max_claim_level": max_claim_level,
        "section_refs": [section["section_ref"] for section in sections],
        "claim_refs": [claim_ref(section) for section in sections],
        "claim_levels": [section["claim_level"] for section in sections],
        "limitations": list(entry["limitations"]),
        "counterevidence": list(entry["counterevidence"]),
        "supported_uses": list(entry["supported_uses"]),
    }


def _make_fetch_by_source(kind: str, tool_name: str) -> Callable[[str], dict[str, Any]]:
    def _handler(topic: str) -> dict[str, Any]:
        chunks = [c for c in BY_TOPIC.get(topic, []) if _matches_kind(c, kind)]
        if not chunks:
            valid = _topics_for_kind(kind)
            return {"error": "unknown topic", "tool": tool_name, "valid_topics": valid}
        c = chunks[0]
        return {
            "topic": topic,
            "content": c["text"],
            "source_ref": c["source_ref"],
            "source_level": c["source_level"],
            "entry_ref": c["entry_ref"],
            "entry_version": c["entry_version"],
            "registry_version": c["registry_version"],
            "max_claim_level": c["max_claim_level"],
            "limitations": c["limitations"],
            "counterevidence": c["counterevidence"],
        }
    return _handler


def make_fetch_kinematics() -> Callable[[str], dict[str, Any]]:
    return _make_fetch_by_source("kinematics", "coach_fetch_kinematics")


def make_fetch_prescription() -> Callable[[str], dict[str, Any]]:
    return _make_fetch_by_source("prescription", "coach_fetch_prescription")


def make_fetch_coaching_theory() -> Callable[[str], dict[str, Any]]:
    return _make_fetch_by_source("theory", "coach_fetch_coaching_theory")


def make_fetch_community_example() -> Callable[[str], dict[str, Any]]:
    return _make_fetch_by_source("community", "coach_fetch_community_example")


# Progress / plan context binders -------------------------------------------


def make_get_trend(trend: dict) -> Callable[[], dict[str, Any]]:
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
    b.add(schema_get_trend(), make_get_trend(trend))
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
    "diagnosis_payload",
    "build_diagnosis_tools",
    "build_progress_tools",
    "build_plan_tools",
]
